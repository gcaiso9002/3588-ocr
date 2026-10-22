'use strict';

document.getElementById('btn-shot').addEventListener('click', () => {
  window.api.send('toolbar:capture');
});
document.getElementById('btn-settings').addEventListener('click', () => {
  window.api.send('toolbar:settings');
});
document.getElementById('btn-quit').addEventListener('click', () => {
  window.api.send('app:quit');
});

// 工具条上悬停就能看到在用哪一版
window.api.version().then((info) => {
  const ver = (info && info.version) || '';
  document.getElementById('bar').title = '3588-ocr v' + ver + '  ·  拖动右侧手柄可移动';
}).catch(() => { /* ignore */ });
