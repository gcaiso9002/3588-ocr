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
