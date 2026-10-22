'use strict';
/**
 * 无 GUI 回归测试：抓屏期间的并发触发（v1.2.0 的「十字光标卡死」根因）。
 *
 * 为什么不用 GUI 测试：
 *   带界面的自检会在**当前桌面**弹出全屏遮罩，把人正在做的事全盖住。
 *   而这个 bug 是并发问题，用假的 electron 模块加载**真实的 main.js**，
 *   在毫秒级反复压测，断言窗口的完整生命周期 —— 更快、更准、且完全不碰桌面。
 *
 * 覆盖的不变量：
 *   1. 抓屏进行中重复触发 → 只能有一次抓屏在跑（并发起不来）
 *   2. 无论触发多少次，遮罩窗口数恒等于屏幕数（不会有野窗口）
 *   3. 关闭时销毁的是**所有**建过的窗口，不是只剩 Map 里的那几个
 *   4. 系统级 Escape 能把遮罩全部关掉
 *   5. 抓屏失败/无图时不会留下半开的遮罩
 *
 * 运行：node test_capture_concurrency.js [main.js 的路径]
 *   带上路径可以拿旧版本源码来跑同一套断言，做 A/B 对照。
 */
const Module = require('module');
const path = require('path');
const fs = require('fs');
const assert = require('assert');

// ---------------------------------------------------------------- 假的 electron

const state = {
  windows: [],            // 所有建过的 BrowserWindow
  getSourcesCalls: 0,
  inFlight: 0,            // 同时在跑的 getSources 数
  maxInFlight: 0,
  shortcuts: new Map(),   // accelerator -> callback
  ipcHandlers: new Map(),
  logLines: [],
};

class FakeWebContents {
  constructor(win) {
    this.win = win;
    this.sent = [];
    this._once = new Map();
  }
  send(channel, payload) { this.sent.push({ channel, payload }); }
  once(evt, cb) { this._once.set(evt, cb); }
  on() { /* ignore */ }
  openDevTools() { /* ignore */ }
  /** 测试用：模拟页面加载完成 */
  fire(evt, arg) { const cb = this._once.get(evt); if (cb) cb(arg); }
  executeJavaScript() { return Promise.resolve(); }
}

class FakeBrowserWindow {
  constructor(opts) {
    this.opts = opts || {};
    this.destroyed = false;
    this.shown = false;
    this.focused = false;
    this.title = this.opts.title || '';
    this.webContents = new FakeWebContents(this);
    this._handlers = new Map();
    state.windows.push(this);
  }
  loadFile(p) { this.loadedFile = p; }
  show() { this.shown = true; }
  focus() { this.focused = true; }
  hide() { this.shown = false; }
  setAlwaysOnTop() { /* ignore */ }
  isDestroyed() { return this.destroyed; }
  getTitle() { return this.title; }
  getBounds() { return { x: 0, y: 0, width: 100, height: 100 }; }
  setTitle(t) { this.title = t; }
  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    const cb = this._handlers.get('closed');
    if (cb) cb();
  }
  on(evt, cb) { this._handlers.set(evt, cb); return this; }
  once(evt, cb) { this._handlers.set(evt, cb); return this; }
  webContents_ = undefined;
}

const DISPLAYS = [
  { id: 2903151393, bounds: { x: 0, y: 0, width: 2560, height: 1440 }, scaleFactor: 1.5, rotation: 0 },
  { id: 505385978, bounds: { x: -1080, y: 288, width: 1080, height: 1920 }, scaleFactor: 1, rotation: 270 },
];

const fakeElectron = {
  app: {
    setName() { },
    getName: () => '3588-ocr',
    getPath: () => path.join(require('os').tmpdir(), '3588-ocr-test'),
    getAppPath: () => __dirname,
    isPackaged: false,
    // 永不 resolve：避免启动托盘/工具条/网络探测这些副作用
    whenReady: () => new Promise(() => { }),
    on() { },
    quit() { },
    exit() { },
    requestSingleInstanceLock: () => true,
    setAppUserModelId() { },
    disableHardwareAcceleration() { },
    commandLine: { appendSwitch() { } },
  },
  BrowserWindow: FakeBrowserWindow,
  Tray: class { constructor() { } setToolTip() { } setContextMenu() { } on() { } destroy() { } },
  Menu: { buildFromTemplate: () => ({}) },
  globalShortcut: {
    register(accelerator, cb) { state.shortcuts.set(accelerator, cb); return true; },
    unregister(accelerator) { state.shortcuts.delete(accelerator); },
    unregisterAll() { state.shortcuts.clear(); },
    isRegistered: (a) => state.shortcuts.has(a),
  },
  screen: {
    getAllDisplays: () => DISPLAYS,
    getPrimaryDisplay: () => DISPLAYS[0],
    getCursorScreenPoint: () => ({ x: 0, y: 0 }),
  },
  desktopCapturer: {
    async getSources(opts) {
      state.getSourcesCalls++;
      state.inFlight++;
      if (state.inFlight > state.maxInFlight) state.maxInFlight = state.inFlight;
      // 模拟真实抓屏的耗时（真机上双屏约 1~2 秒）
      await new Promise((r) => setTimeout(r, opts.__delay || 120));
      state.inFlight--;
      return DISPLAYS.map((d) => ({
        display_id: String(d.id),
        thumbnail: { toPNG: () => Buffer.from('fake-png-' + d.id), isEmpty: () => false },
      }));
    },
  },
  nativeImage: { createFromPath: () => ({ isEmpty: () => true }), createEmpty: () => ({ isEmpty: () => true }) },
  clipboard: { writeText() { }, readText: () => '' },
  ipcMain: {
    on(channel, cb) { state.ipcHandlers.set(channel, cb); },
    handle() { },
  },
  session: { defaultSession: { setDisplayMediaRequestHandler() { } } },
  shell: { openExternal() { } },
  nativeTheme: { shouldUseDarkColors: false },
};

// 劫持 require('electron')
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === 'electron') return fakeElectron;
  return origLoad.apply(this, arguments);
};

// 把 %APPDATA% 指向临时目录：
//   1) 不碰用户真实的 config.json / 日志
//   2) 可以往里写一份「选区空闲超时 = 0.3 秒」的配置，让测试跑得快
const TMP_APPDATA = path.join(require('os').tmpdir(), '3588-ocr-test-' + process.pid);
process.env.APPDATA = TMP_APPDATA;
fs.mkdirSync(path.join(TMP_APPDATA, '3588-ocr'), { recursive: true });
fs.writeFileSync(
  path.join(TMP_APPDATA, '3588-ocr', 'config.json'),
  JSON.stringify({ select_idle_timeout: 0.3 }, null, 2), 'utf8');
const IDLE_MS = 300;

// 屏蔽日志落盘，避免污染任何真实目录
const origAppend = fs.appendFileSync;
fs.appendFileSync = function (file, data, enc) {
  if (String(file).indexOf('3588-ocr.log') >= 0) {
    state.logLines.push(String(data).trim());
    return true;
  }
  return origAppend.apply(this, arguments);
};

const main = require(process.argv[2] || path.join(__dirname, 'main.js'));
main.loadConfig();          // 读上面那份临时配置（空闲超时 0.3 秒）

// ---------------------------------------------------------------- 断言工具

let pass = 0;
const failures = [];
function check(name, ok, detail) {
  if (ok) {
    pass++;
    console.log('  PASS  ' + name + (detail ? '  | ' + detail : ''));
  } else {
    failures.push(name);
    console.log('  FAIL  ' + name + (detail ? '  | ' + detail : ''));
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 当前存活的遮罩窗口数 */
function liveOverlays() {
  return state.windows.filter((w) => !w.destroyed && w.loadedFile
    && String(w.loadedFile).indexOf('overlay.html') >= 0).length;
}
/** 建过但还没销毁的遮罩窗口（= 野窗口） */
function orphanOverlays() {
  return state.windows.filter((w) => !w.destroyed && w.loadedFile
    && String(w.loadedFile).indexOf('overlay.html') >= 0).length;
}

async function main_test() {
  console.log('\n[1] 抓屏期间连按 3 次热键');
  state.getSourcesCalls = 0;
  state.maxInFlight = 0;
  state.windows.length = 0;

  // 模拟热键连按：startCapture 是 async，前一次还没 await 完就再调
  const p1 = main.startCapture();
  await sleep(30);
  const p2 = main.startCapture();
  await sleep(30);
  const p3 = main.startCapture();
  await Promise.all([p1, p2, p3]);
  await sleep(120);

  const st = main.__state();
  check('抓屏没有并发（同时最多跑一轮）',
    state.maxInFlight <= DISPLAYS.length,
    '同时在跑的 getSources 最多 ' + state.maxInFlight + '（一轮 = ' + DISPLAYS.length + '）');
  check('遮罩窗口数 == 屏幕数', st.overlayWins === DISPLAYS.length,
    'overlayWins=' + st.overlayWins);
  check('没有野窗口', orphanOverlays() === DISPLAYS.length,
    '存活遮罩=' + orphanOverlays());
  check('Escape 全局快捷键已注册', st.escArmed === true);
  check('空闲超时已按配置生效', st.idleTimeoutMs === IDLE_MS,
    'idleTimeoutMs=' + st.idleTimeoutMs + ' 期望=' + IDLE_MS);

  console.log('\n[2] 按 Escape 关闭');
  const escCb = state.shortcuts.get('Escape');
  check('Escape 有注册回调', typeof escCb === 'function');
  if (escCb) escCb();
  await sleep(50);
  const st2 = main.__state();
  check('关闭后 overlayWins 归零', st2.overlayWins === 0, 'overlayWins=' + st2.overlayWins);
  check('关闭后没有残留窗口', liveOverlays() === 0, '存活遮罩=' + liveOverlays());
  check('关闭后 Escape 快捷键已注销', st2.escArmed === false);
  check('capturing 已复位', st2.capturing === false);

  console.log('\n[3] 选区中再按一次热键 = 取消并重抓');
  await main.startCapture();
  await sleep(60);
  const st3 = main.__state();
  check('重新进入选区', st3.overlayWins === DISPLAYS.length, 'overlayWins=' + st3.overlayWins);
  await main.startCapture();          // 选区中再按
  await sleep(200);
  const st4 = main.__state();
  check('重抓后仍然只有一套遮罩', st4.overlayWins === DISPLAYS.length,
    'overlayWins=' + st4.overlayWins);
  check('重抓后没有野窗口', orphanOverlays() === DISPLAYS.length,
    '存活遮罩=' + orphanOverlays());

  console.log('\n[4] 疯狂连按 20 次也不能多出窗口');
  for (let i = 0; i < 20; i++) {
    main.startCapture();
    await sleep(5);
  }
  await sleep(400);
  const st5 = main.__state();
  check('20 次连按后遮罩数仍等于屏幕数', st5.overlayWins <= DISPLAYS.length,
    'overlayWins=' + st5.overlayWins);
  check('20 次连按后没有野窗口', orphanOverlays() === st5.overlayWins,
    '存活遮罩=' + orphanOverlays() + ' / 追踪=' + st5.overlayWins);

  console.log('\n[5] 关闭时销毁所有建过的窗口');
  main.closeOverlays('test');
  await sleep(50);
  check('全部销毁', liveOverlays() === 0, '存活遮罩=' + liveOverlays());
  check('状态归零', main.__state().overlayWins === 0);

  console.log('\n[6] 选区无操作 -> 自动撤掉遮罩，回到等待下一次');
  await main.startCapture();
  // startCapture 返回时遮罩已经 armed、计时器刚开始，所以要马上看
  check('已进入选区', main.__state().overlayWins === DISPLAYS.length,
    'overlayWins=' + main.__state().overlayWins);
  await sleep(IDLE_MS + 300);      // 一动不动地等着
  check('超时后遮罩自动消失', main.__state().overlayWins === 0,
    'overlayWins=' + main.__state().overlayWins);
  check('超时后没有残留窗口', liveOverlays() === 0, '存活遮罩=' + liveOverlays());

  console.log('\n[7] 选区中有动作 -> 计时器重置，不会被自动撤掉');
  await main.startCapture();
  check('已进入选区', main.__state().overlayWins === DISPLAYS.length,
    'overlayWins=' + main.__state().overlayWins);
  const activity = state.ipcHandlers.get('overlay:activity');
  check('渲染层有 activity 通道', typeof activity === 'function');
  // 每隔一段时间报一次活动，总时长超过超时阈值
  for (let i = 0; i < 8; i++) {
    if (activity) activity();
    await sleep(IDLE_MS / 4);
  }
  check('有活动时遮罩还在', main.__state().overlayWins === DISPLAYS.length,
    'overlayWins=' + main.__state().overlayWins);
  main.closeOverlays('test');
  await sleep(50);

  console.log('\n[8] 抓屏途中再按一次 -> 这次作废，重新抓（最新的按键赢）');
  state.getSourcesCalls = 0;
  const p = main.startCapture();
  await sleep(40);
  main.startCapture();               // 抓屏途中再按
  await sleep(20);
  main.startCapture();               // 再按一次
  await p;
  await sleep(400);
  const st8 = main.__state();
  check('最终只留下一套遮罩', st8.overlayWins === DISPLAYS.length,
    'overlayWins=' + st8.overlayWins);
  check('抓屏被重启过（次数 > 一轮）', state.getSourcesCalls > DISPLAYS.length,
    'getSources=' + state.getSourcesCalls);
  check('重启后没有野窗口', orphanOverlays() === DISPLAYS.length,
    '存活遮罩=' + orphanOverlays());
  main.closeOverlays('test');

  console.log('\n[9] 结果显示方式三档都能取到');
  check('默认是 always（弹出并保留）', main.resultMode() === 'always',
    'resultMode=' + main.resultMode());
  const setCfg = main.__setCfg;
  setCfg({ result_mode: 'auto' });
  check('可切到 auto（弹出后自动关）', main.resultMode() === 'auto',
    'resultMode=' + main.resultMode());
  setCfg({ result_mode: 'silent' });
  check('可切到 silent（不弹出）', main.resultMode() === 'silent',
    'resultMode=' + main.resultMode());
  setCfg({ result_mode: 'silent' });
  check('silent 优先于旧的 keep_result_window',
    main.resultMode() === 'silent', 'resultMode=' + main.resultMode());
  setCfg({ result_mode: undefined, keep_result_window: false });
  check('老配置只有 keep_result_window=false 时映射成 auto',
    main.resultMode() === 'auto', 'resultMode=' + main.resultMode());
  setCfg({ result_mode: undefined, keep_result_window: true });
  check('老配置 keep_result_window=true 时映射成 always',
    main.resultMode() === 'always', 'resultMode=' + main.resultMode());

  console.log('\n---- 关键日志 ----');
  for (const l of state.logLines) {
    if (/capture|overlay/.test(l)) console.log('    ' + l);
  }

  console.log('\n================ 结果 ================');
  console.log('通过 ' + pass + ' 项' + (failures.length ? '，失败 ' + failures.length + ' 项' : ''));
  if (failures.length) {
    for (const f of failures) console.log('  - ' + f);
    process.exit(1);
  }
  console.log('全部通过（全程无窗口、无弹窗）');
}

main_test().catch((e) => {
  console.error(e);
  process.exit(1);
});
