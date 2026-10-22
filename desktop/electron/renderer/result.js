'use strict';

const $ = (id) => document.getElementById(id);
const textEl = $('text');
const metaEl = $('meta');
const tipEl = $('tip');
const noticeEl = $('notice');

let tipTimer = null;
let autoCloseTimer = null;

function tip(msg, kind) {
  tipEl.textContent = msg;
  tipEl.className = 'tip show ' + (kind || '');
  clearTimeout(tipTimer);
  tipTimer = setTimeout(() => { tipEl.className = 'tip'; }, 2200);
}

function showNotice(data) {
  $('notice-title').textContent = data.title || '';
  $('notice-text').textContent = data.message || '';
  noticeEl.className = 'notice ' + (data.kind || 'info');
  noticeEl.hidden = false;
}

$('notice-x').addEventListener('click', () => { noticeEl.hidden = true; });

window.api.on('result:data', (d) => {
  textEl.value = d.text || '';
  noticeEl.hidden = true;
  const bits = [];
  if (d.lines) bits.push(d.lines + ' 行');
  if (d.elapsed) bits.push(d.elapsed.toFixed(2) + 's');
  metaEl.textContent = bits.join(' · ');
  textEl.focus();
  textEl.setSelectionRange(0, 0);
  autoGrow();
  // 「弹出后自动关闭」模式：倒计时结束就关掉
  if (autoCloseTimer) { clearTimeout(autoCloseTimer); autoCloseTimer = null; }
  if (d.autoCloseMs) {
    const sec = Math.round(d.autoCloseMs / 1000);
    tip(sec + ' 秒后自动关闭', '');
    autoCloseTimer = setTimeout(() => {
      window.api.send('result:close');
    }, d.autoCloseMs);
  }
});

window.api.on('result:notice', (d) => showNotice(d));

function autoGrow() {
  // 面板高度跟着文字走，但不超过窗口
  textEl.style.height = 'auto';
  const max = window.innerHeight - 190;
  textEl.style.height = Math.min(textEl.scrollHeight, Math.max(120, max)) + 'px';
}
window.addEventListener('resize', autoGrow);

/** 用户开始动手了（复制/粘贴/改文字）-> 别再自动把面板收走 */
function cancelAutoClose() {
  if (!autoCloseTimer) return;
  clearTimeout(autoCloseTimer);
  autoCloseTimer = null;
}

$('btn-copy').addEventListener('click', async () => {
  cancelAutoClose();
  await window.api.copy(textEl.value);
  tip('已复制到剪贴板', 'ok');
});

$('btn-paste').addEventListener('click', async () => {
  cancelAutoClose();
  await window.api.paste(textEl.value);
  tip('已粘贴到原窗口', 'ok');
});

textEl.addEventListener('input', cancelAutoClose);

$('btn-rescan').addEventListener('click', () => {
  window.api.send('result:rescan');
  tip('重新识别中…');
});

$('btn-settings').addEventListener('click', () => {
  window.api.send('result:open-settings');
});

$('btn-close').addEventListener('click', () => {
  window.api.send('result:close');
});

// Ctrl+C / Ctrl+V 在文本区里正常工作；Ctrl+Enter 复制
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.ctrlKey) {
    e.preventDefault();
    $('btn-copy').click();
  }
  if (e.key === 'Escape') {
    window.api.send('result:close');
  }
});
