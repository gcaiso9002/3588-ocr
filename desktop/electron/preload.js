'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  // ---- 应用信息
  version: () => ipcRenderer.invoke('app:version'),

  // ---- 配置
  getConfig: () => ipcRenderer.invoke('cfg:get'),
  defaults: () => ipcRenderer.invoke('cfg:defaults'),
  saveConfig: (patch) => ipcRenderer.invoke('cfg:save', patch),
  testServer: (url) => ipcRenderer.invoke('cfg:test', url),
  discover: () => ipcRenderer.invoke('cfg:discover'),
  revealConfig: () => ipcRenderer.invoke('cfg:reveal'),
  openRepo: () => ipcRenderer.invoke('cfg:open-repo'),

  // ---- 剪贴板
  copy: (text) => ipcRenderer.invoke('clip:copy', text),
  paste: (text) => ipcRenderer.invoke('clip:paste', text),
  readClipboard: () => ipcRenderer.invoke('clip:read'),

  // ---- 消息
  send: (channel, ...args) => ipcRenderer.send(channel, ...args),
  on: (channel, cb) => {
    const listener = (_e, data) => cb(data);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  },
});
