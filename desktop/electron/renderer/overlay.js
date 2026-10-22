'use strict';
/**
 * 选区遮罩层（每块屏一个窗口）
 *
 * 窗口正好铺满一块屏幕，clientX/clientY 就是这块屏内的 DIP 坐标。
 * 画布后端缓冲是物理像素，所以裁剪时乘 scaleFactor 得到 1:1 的物理图。
 */

const shot = document.getElementById('shot');
const sel = document.getElementById('sel');
const hline = document.getElementById('hline');
const vline = document.getElementById('vline');
const mag = document.getElementById('mag');
const sizeTag = document.getElementById('size');
const hint = document.getElementById('hint');
const ctx = shot.getContext('2d');
const magCtx = mag.getContext('2d');

const state = {
  displayId: null,
  scaleFactor: 1,
  width: 0,
  height: 0,
  magnifier: true,
  armed: false,
  ready: false,
};

let start = null;
let cur = null;
let dragging = false;
let busy = false;

window.api.on('overlay:init', (info) => {
  state.displayId = info.displayId;
  state.scaleFactor = info.scaleFactor || 1;
  state.width = info.width;
  state.height = info.height;
  state.magnifier = !!info.magnifier;

  // 后端缓冲用物理像素，显示尺寸用 DIP —— 屏幕上的位置与像素完全对得上
  shot.width = Math.max(1, Math.round(info.width * state.scaleFactor));
  shot.height = Math.max(1, Math.round(info.height * state.scaleFactor));
  shot.style.width = info.width + 'px';
  shot.style.height = info.height + 'px';

  const img = new Image();
  img.onload = () => {
    ctx.drawImage(img, 0, 0, shot.width, shot.height);
    state.ready = true;
    console.log('overlay: image drawn', img.naturalWidth, 'x', img.naturalHeight,
      '-> canvas', shot.width, 'x', shot.height);
  };
  img.onerror = (e) => {
    console.error('overlay: image failed to load: ' + info.shot);
  };
  img.src = info.shot;
});

window.api.on('overlay:armed', () => {
  state.armed = true;
  hint.style.display = 'flex';
});

function rect() {
  return {
    x: Math.min(start.x, cur.x),
    y: Math.min(start.y, cur.y),
    w: Math.abs(cur.x - start.x),
    h: Math.abs(cur.y - start.y),
  };
}

function paint() {
  const r = rect();
  sel.style.display = 'block';
  sel.style.left = r.x + 'px';
  sel.style.top = r.y + 'px';
  sel.style.width = r.w + 'px';
  sel.style.height = r.h + 'px';

  hline.style.display = 'block';
  hline.style.top = cur.y + 'px';
  vline.style.display = 'block';
  vline.style.left = cur.x + 'px';

  sizeTag.style.display = 'block';
  sizeTag.textContent = Math.round(r.w) + ' × ' + Math.round(r.h);
  const tw = sizeTag.offsetWidth || 60;
  const th = sizeTag.offsetHeight || 20;
  let sx = r.x + r.w / 2 - tw / 2;
  let sy = r.y - th - 6;
  if (sy < 2) sy = r.y + r.h + 6;
  sx = Math.max(2, Math.min(sx, window.innerWidth - tw - 2));
  sy = Math.max(2, Math.min(sy, window.innerHeight - th - 2));
  sizeTag.style.left = sx + 'px';
  sizeTag.style.top = sy + 'px';
}

function paintMagnifier() {
  if (!state.magnifier || !state.ready) { mag.style.display = 'none'; return; }
  const box = mag.width;
  const s = state.scaleFactor;
  const srcSpan = Math.round(34 * s);          // 放大镜里显示多大一块（物理像素）
  let sx = Math.round(cur.x * s - srcSpan / 2);
  let sy = Math.round(cur.y * s - srcSpan / 2);
  sx = Math.max(0, Math.min(sx, shot.width - srcSpan));
  sy = Math.max(0, Math.min(sy, shot.height - srcSpan));

  magCtx.imageSmoothingEnabled = false;
  magCtx.clearRect(0, 0, box, box);
  magCtx.drawImage(shot, sx, sy, srcSpan, srcSpan, 0, 0, box, box);
  // 十字线
  magCtx.strokeStyle = 'rgba(255, 86, 86, .9)';
  magCtx.lineWidth = 1;
  magCtx.beginPath();
  magCtx.moveTo(box / 2, box / 2 - 10); magCtx.lineTo(box / 2, box / 2 + 10);
  magCtx.moveTo(box / 2 - 10, box / 2); magCtx.lineTo(box / 2 + 10, box / 2);
  magCtx.stroke();

  mag.style.display = 'block';
  let mx = cur.x + 22;
  let my = cur.y + 22;
  if (mx + box + 8 > window.innerWidth) mx = cur.x - box - 22;
  if (my + box + 8 > window.innerHeight) my = cur.y - box - 22;
  mag.style.left = Math.max(0, mx) + 'px';
  mag.style.top = Math.max(0, my) + 'px';
}

/** 从画布上按 1:1 物理像素裁出选区 */
function crop(r) {
  const s = state.scaleFactor;
  const sx = Math.max(0, Math.round(r.x * s));
  const sy = Math.max(0, Math.round(r.y * s));
  const sw = Math.min(shot.width - sx, Math.round(r.w * s));
  const sh = Math.min(shot.height - sy, Math.round(r.h * s));
  if (sw < 2 || sh < 2) return null;
  const c = document.createElement('canvas');
  c.width = sw;
  c.height = sh;
  const cx = c.getContext('2d');
  cx.imageSmoothingEnabled = false;
  cx.drawImage(shot, sx, sy, sw, sh, 0, 0, sw, sh);
  return c.toDataURL('image/png');
}

// ---- 空闲保活 -------------------------------------------------
// 主进程那边有个「选区多久没动静就自动取消」的计时器。
// 用户在遮罩上的一举一动都要报过去，计时器才会重新开始。
let lastActivityAt = 0;
let dragHeartbeat = null;

function reportActivity() {
  const now = Date.now();
  if (now - lastActivityAt < 100) return;   // 节流，别把 IPC 打爆
  lastActivityAt = now;
  window.api.send('overlay:activity');
}

function startDragHeartbeat() {
  if (dragHeartbeat) return;
  // 拖拽过程中鼠标可能停住不动，这时也要保证计时器别把遮罩收走
  dragHeartbeat = setInterval(() => window.api.send('overlay:activity'), 400);
}

function stopDragHeartbeat() {
  if (!dragHeartbeat) return;
  clearInterval(dragHeartbeat);
  dragHeartbeat = null;
}

document.addEventListener('mousedown', (e) => {
  if (!state.armed || !state.ready || busy || e.button !== 0) return;
  e.preventDefault();
  start = { x: e.clientX, y: e.clientY };
  cur = { x: e.clientX, y: e.clientY };
  dragging = true;
  hint.style.display = 'none';
  reportActivity();
  startDragHeartbeat();
  paint();
  paintMagnifier();
});

document.addEventListener('mousemove', (e) => {
  reportActivity();
  if (!dragging) return;
  cur = { x: e.clientX, y: e.clientY };
  paint();
  paintMagnifier();
});

document.addEventListener('mouseup', (e) => {
  if (!dragging || busy || e.button !== 0) return;
  dragging = false;
  stopDragHeartbeat();
  cur = { x: e.clientX, y: e.clientY };
  const r = rect();
  if (r.w < 8 || r.h < 8) {
    window.api.send('overlay:cancel');
    return;
  }
  const dataUrl = crop(r);
  if (!dataUrl) {
    window.api.send('overlay:cancel');
    return;
  }
  busy = true;
  sel.style.display = 'none';
  sizeTag.style.display = 'none';
  mag.style.display = 'none';
  hline.style.display = 'none';
  vline.style.display = 'none';
  window.api.send('overlay:select', {
    displayId: state.displayId,
    dataUrl,
    rect: { x: r.x, y: r.y, w: r.w, h: r.h },
  });
});

document.addEventListener('keydown', (e) => {
  reportActivity();
  if (e.key === 'Escape') {
    e.preventDefault();
    window.api.send('overlay:cancel');
  }
});

// 右键 = 取消（多一个兜底出口，万一 Esc 被别的窗口吃掉）
window.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  if (!busy) window.api.send('overlay:cancel');
});
