'use strict';

const $ = (id) => document.getElementById(id);

let cfg = null;
let defaults = null;
let dirty = false;

/* ------------------------------------------------ 导航 */

document.querySelectorAll('.nav').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
    document.querySelector('.page[data-page="' + btn.dataset.page + '"]').classList.add('active');
  });
});

/* ------------------------------------------------ 读表单 */

function readForm() {
  return {
    server: $('server').value.trim(),
    server_candidates: $('candidates').value.split(',').map((s) => s.trim()).filter(Boolean),
    hotkey: $('hotkey').value.trim(),
    auto_copy: $('auto_copy').checked,
    auto_paste: $('auto_paste').checked,
    result_mode: $('result_mode').value,
    keep_result_window: $('result_mode').value === 'always',
    show_toolbar: $('show_toolbar').checked,
    start_minimized: $('start_minimized').checked,
    merge_lines: $('merge_lines').checked,
    trim_spaces: $('trim_spaces').checked,
    magnifier: $('magnifier').checked,
    ocr_timeout: parseInt($('ocr_timeout').value, 10) || 60,
    select_idle_timeout: Math.max(0, parseInt($('select_idle_timeout').value, 10) || 0),
  };
}

function fillForm(c) {
  $('server').value = c.server || '';
  $('candidates').value = (c.server_candidates || []).join(', ');
  $('hotkey').value = c.hotkey || 'ctrl+1';
  $('auto_copy').checked = !!c.auto_copy;
  $('auto_paste').checked = !!c.auto_paste;
  // 老配置里只有 keep_result_window，这里兼容一下
  $('result_mode').value = c.result_mode
    || (c.keep_result_window === false ? 'auto' : 'always');
  $('show_toolbar').checked = !!c.show_toolbar;
  $('start_minimized').checked = !!c.start_minimized;
  $('merge_lines').checked = !!c.merge_lines;
  $('trim_spaces').checked = !!c.trim_spaces;
  $('magnifier').checked = !!c.magnifier;
  $('ocr_timeout').value = c.ocr_timeout || 60;
  $('select_idle_timeout').value = (c.select_idle_timeout === undefined ? 2 : c.select_idle_timeout);
}

/* ------------------------------------------------ 脏标记 */

function markDirty() {
  dirty = JSON.stringify(readForm()) !== JSON.stringify(stripExtra(cfg));
  $('dirty').textContent = dirty ? '有未保存的修改' : '';
}
function stripExtra(c) {
  const o = {};
  for (const k of Object.keys(readForm())) o[k] = c[k];
  return o;
}
document.querySelectorAll('input').forEach((el) => {
  el.addEventListener('input', markDirty);
  el.addEventListener('change', markDirty);
});

/* ------------------------------------------------ 板子状态 */

function setBoard(ok, text) {
  const dot = document.querySelector('#board-state .dot');
  dot.className = 'dot ' + (ok ? 'ok' : 'bad');
  $('board-text').textContent = text;
}

async function refreshBoard() {
  setBoard(null, '检测中…');
  const url = $('server').value.trim();
  if (!url) { setBoard(false, '未设置地址'); return; }
  const r = await window.api.testServer(url);
  if (r.ok) {
    setBoard(true, '板子在线 · ' + url.replace(/^https?:\/\//, ''));
  } else {
    setBoard(false, '板子离线');
  }
}

$('btn-test').addEventListener('click', async () => {
  const msg = $('server-msg');
  msg.className = 'inline-msg busy';
  msg.textContent = '测试中…';
  const r = await window.api.testServer($('server').value.trim());
  msg.className = 'inline-msg ' + (r.ok ? 'ok' : 'err');
  msg.textContent = r.message;
  setBoard(r.ok, r.ok ? '板子在线' : '板子离线');
});

$('btn-find').addEventListener('click', async () => {
  const msg = $('server-msg');
  msg.className = 'inline-msg busy';
  msg.textContent = '正在扫描本地网段…';
  const r = await window.api.discover();
  if (r.ok) {
    $('server').value = r.server;
    msg.className = 'inline-msg ok';
    msg.textContent = '找到：' + r.server;
    setBoard(true, '板子在线 · ' + r.server.replace(/^https?:\/\//, ''));
  } else {
    msg.className = 'inline-msg err';
    msg.textContent = '没找到板子，确认板子已开机并插好网线';
    setBoard(false, '板子离线');
  }
  markDirty();
});

/* ------------------------------------------------ 快捷键录制 */

const hkInput = $('hotkey');
let recording = false;

hkInput.addEventListener('focus', () => {
  recording = true;
  hkInput.classList.add('recording');
  hkInput.value = '按下组合键…';
});

hkInput.addEventListener('blur', () => {
  recording = false;
  hkInput.classList.remove('recording');
  if (cfg) hkInput.value = cfg.hotkey || 'ctrl+1';
});

hkInput.addEventListener('keydown', (e) => {
  if (!recording) return;
  e.preventDefault();
  if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return;
  const parts = [];
  if (e.ctrlKey) parts.push('ctrl');
  if (e.altKey) parts.push('alt');
  if (e.shiftKey) parts.push('shift');
  if (e.metaKey) parts.push('win');
  let key = e.key;
  if (key === ' ') key = 'space';
  if (key.length === 1) key = key.toLowerCase();
  if (!parts.length && key === 'Escape') { hkInput.blur(); return; }
  if (!parts.length) return;          // 必须带修饰键
  parts.push(key);
  const spec = parts.join('+');
  hkInput.value = spec;
  $('hotkey-msg').className = 'inline-msg ok';
  $('hotkey-msg').textContent = '已记录：' + spec;
  hkInput.blur();
  markDirty();
});

/* ------------------------------------------------ 保存 / 取消 / 重置 */

$('btn-save').addEventListener('click', async () => {
  const patch = readForm();
  if (!patch.server) {
    alert('请填写板子服务地址');
    return;
  }
  await window.api.saveConfig(patch);
  dirty = false;
  $('dirty').textContent = '';
  $('server-msg').className = 'inline-msg ok';
  $('server-msg').textContent = '已保存';
  setTimeout(() => window.api.send('win:close'), 250);
});

$('btn-cancel').addEventListener('click', () => window.api.send('win:close'));

$('btn-reset').addEventListener('click', () => {
  fillForm(defaults);
  markDirty();
  $('server-msg').className = 'inline-msg';
  $('server-msg').textContent = '已恢复默认值，点「保存」生效';
});

$('btn-reveal').addEventListener('click', () => window.api.revealConfig());
$('btn-repo').addEventListener('click', () => window.api.openRepo());
$('btn-min').addEventListener('click', () => window.api.send('win:minimize'));
$('btn-close').addEventListener('click', () => window.api.send('win:close'));

/* ------------------------------------------------ 启动 */

(async function init() {
  cfg = await window.api.getConfig();
  defaults = await window.api.defaults();
  fillForm(cfg);
  markDirty();
  refreshBoard();
})();
