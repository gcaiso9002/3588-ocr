'use strict';
/**
 * 3588-ocr — RK3588 NPU 截图取字客户端（Electron 主进程）
 *
 * 识别全部在板子的 NPU 上完成，本进程只负责：
 *   抓屏 → 选区 → 裁剪 → 发 HTTP → 显示结果 → 写剪贴板
 */
const {
  app, BrowserWindow, Tray, Menu, globalShortcut, screen, desktopCapturer,
  nativeImage, clipboard, ipcMain, session, shell, nativeTheme,
} = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const dgram = require('dgram');
const { execFile } = require('child_process');

app.setName('3588-ocr');

const HERE = __dirname;
const RENDERER = path.join(HERE, 'renderer');
const ASSETS = path.join(HERE, 'assets');
const ICON_PNG = path.join(ASSETS, 'icon.png');
const FOCUS_PS = path.join(HERE, 'native', 'focus.ps1');

// ---------------------------------------------------------------- 配置

const CONFIG_DIR = path.join(process.env.APPDATA || os.homedir(), '3588-ocr');
const CONFIG_PATH = path.join(CONFIG_DIR, 'config.json');
const LOG_PATH = path.join(CONFIG_DIR, '3588-ocr.log');
const SHOT_DIR = path.join(os.tmpdir(), '3588-ocr');

const DEFAULT_CFG = {
  server: 'http://192.168.31.230:8766',
  server_candidates: ['http://192.168.31.40:8766'],
  hotkey: 'ctrl+1',
  auto_copy: true,
  auto_paste: false,
  magnifier: true,
  keep_result_window: false,
  merge_lines: false,
  trim_spaces: false,
  show_toolbar: true,
  start_minimized: true,
  ocr_timeout: 60,
};

let cfg = { ...DEFAULT_CFG };

function loadConfig() {
  try {
    // PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会写 BOM，JSON.parse 会直接抛
    const raw = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8').replace(/^\uFEFF/, ''));
    cfg = { ...DEFAULT_CFG, ...raw };
  } catch (e) {
    cfg = { ...DEFAULT_CFG };
  }
  return cfg;
}

function saveConfig(patch) {
  cfg = { ...cfg, ...patch };
  try {
    fs.mkdirSync(CONFIG_DIR, { recursive: true });
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2), 'utf8');
  } catch (e) {
    log('cannot save config: ' + e);
  }
  return cfg;
}

function log(msg) {
  const line = new Date().toLocaleTimeString('zh-CN', { hour12: false }) + '  ' + msg;
  console.log(line);
  try {
    fs.mkdirSync(CONFIG_DIR, { recursive: true });
    fs.appendFileSync(LOG_PATH, line + '\n', 'utf8');
  } catch (e) { /* 忽略 */ }
}

// ---------------------------------------------------------------- 状态

let tray = null;
let resultWin = null;
let settingsWin = null;
let toolbarWin = null;
/** @type {Map<string, BrowserWindow>} displayId -> 选区窗口 */
const overlays = new Map();
/**
 * 所有曾经创建过的选区窗口。
 * overlays 是 Map，只留每个屏幕的最后一个；一旦并发创建出多余窗口，
 * 它们就会从 Map 里掉出去、变成关不掉的野窗口（十字光标卡死）。
 * 这里额外用 Set 兜底，closeOverlays() 会把它们全部销毁。
 * @type {Set<BrowserWindow>}
 */
const overlayWins = new Set();
/** 正在抓屏（await 中），用于防止连按热键导致并发抓屏 */
let capturing = false;
let lastFgHwnd = null;        // 截图前的前台窗口句柄
let lastResult = null;        // { text, lines, elapsed, rect }

// ---------------------------------------------------------------- 板子服务

async function serverHealth(url, timeout = 4000) {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout);
    const res = await fetch(String(url).replace(/\/+$/, '') + '/health', {
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!res.ok) return null;
    const data = await res.json();
    // 必须含 det 字段，否则可能是同端口的别的服务
    return (data && data.ok && 'det' in data) ? data : null;
  } catch (e) {
    return null;
  }
}

/** 本机出站流量会用的那个 IP —— 用 UDP connect 让系统选路由，不发包 */
function primaryLocalIp() {
  return new Promise((resolve) => {
    let settled = false;
    const done = (ip) => {
      if (settled) return;
      settled = true;
      try { sock.close(); } catch (e) { /* ignore */ }
      resolve(ip);
    };
    const sock = dgram.createSocket('udp4');
    sock.on('error', () => done(null));
    setTimeout(() => done(null), 1500);
    try {
      // UDP connect 不会发包，只是让系统按路由表选源地址
      sock.connect(80, '8.8.8.8', () => {
        try {
          done(sock.address().address);
        } catch (e) {
          done(null);
        }
      });
    } catch (e) {
      done(null);
    }
  });
}

async function scanSubnet(baseIp, port = 8766, timeout = 350, workers = 96) {
  const parts = String(baseIp).split('.');
  if (parts.length !== 4) return null;
  const targets = [];
  for (let i = 1; i < 255; i++) {
    // 注意：JavaScript 没有 printf 风格的 % 占位符，必须用模板字符串
    targets.push(`http://${parts[0]}.${parts[1]}.${parts[2]}.${i}:${port}`);
  }
  let idx = 0;
  let found = null;
  const workersN = Math.min(workers, targets.length);
  async function worker() {
    while (found === null) {
      const myIdx = idx++;
      if (myIdx >= targets.length) return;
      const url = targets[myIdx];
      const data = await serverHealth(url, timeout);
      if (data) found = url;
    }
  }
  await Promise.all(Array.from({ length: workersN }, worker));
  return found;
}

async function discoverServer({ scan = true } = {}) {
  const list = [];
  if (cfg.server) list.push(cfg.server);
  for (const c of (cfg.server_candidates || [])) list.push(c);
  for (const url of list) {
    if (await serverHealth(url)) return url;
  }
  if (!scan) return null;
  const base = await primaryLocalIp();
  if (!base) {
    log('cannot determine the local ip, scan skipped');
    return null;
  }
  log('scanning the local subnet ' + base + '.0/24 for an npu-ocr service ...');
  // 板子可能正在重启 / 刚开机，第一次扫不到时隔几秒再扫两次
  for (let attempt = 1; attempt <= 3; attempt++) {
    const hit = await scanSubnet(base);
    if (hit) {
      log('found an npu-ocr service at ' + hit);
      saveConfig({ server: hit });
      return hit;
    }
    log('no npu-ocr service found on the local subnet (attempt ' + attempt + '/3)');
    if (attempt < 3) await new Promise((r) => setTimeout(r, 4000));
  }
  return null;
}

/** 用板子做一次 OCR，返回 { text, lines, elapsed } */
async function ocrImage(server, pngBuffer) {
  const form = new FormData();
  form.append('file', new Blob([pngBuffer], { type: 'image/png' }), 'shot.png');
  const t0 = Date.now();
  const res = await fetch(String(server).replace(/\/+$/, '') + '/ocr', {
    method: 'POST',
    body: form,
    signal: AbortSignal.timeout((cfg.ocr_timeout || 60) * 1000),
  });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  let payload = await res.json();
  if (Array.isArray(payload)) payload = payload[0];
  const lines = (payload && payload.lines) || [];
  const text = lines.map((l) => l.text).join('\n');
  return { text, lines, elapsed: (Date.now() - t0) / 1000 };
}

// ---------------------------------------------------------------- 热键

const HOTKEY_KEYS = ['2', '3', '4', '5', '6', '7', '8', '9', '0',
  'q', 'w', 'e', 'r', 's', 'd', 'f', 'g', 'z', 'x', 'c', 'v', 'b',
  'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8'];

/** 'ctrl+1' -> 'CommandOrControl+1' */
function toElectronHotkey(spec) {
  const parts = String(spec).split('+').map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!parts.length) return null;
  const out = [];
  for (const p of parts) {
    if (p === 'ctrl') out.push('CommandOrControl');
    else if (p === 'win') out.push('Super');
    else if (p === 'alt') out.push('Alt');
    else if (p === 'shift') out.push('Shift');
    else out.push(p.length === 1 ? p.toUpperCase() : p);
  }
  return out.join('+');
}

function hotkeyAlternatives(spec) {
  const parts = String(spec).split('+').map((s) => s.trim().toLowerCase()).filter(Boolean);
  const mods = parts.filter((p) => ['ctrl', 'alt', 'shift', 'win'].includes(p));
  const out = [];
  for (const k of HOTKEY_KEYS) out.push([...mods, k].join('+'));
  for (const m of ['ctrl+alt', 'ctrl+shift', 'alt+shift']) {
    for (const k of HOTKEY_KEYS) out.push([m, k].join('+'));
  }
  out.unshift(spec);
  return out;
}

let currentHotkey = null;

function registerHotkey(spec, quiet) {
  if (currentHotkey) {
    globalShortcut.unregister(currentHotkey);
    currentHotkey = null;
  }
  const primary = toElectronHotkey(spec);
  if (primary && globalShortcut.register(primary, onHotkey)) {
    currentHotkey = primary;
    log('hotkey registered: ' + primary);
    return { ok: true, hotkey: primary };
  }
  for (const alt of hotkeyAlternatives(spec)) {
    const e = toElectronHotkey(alt);
    if (e && globalShortcut.register(e, onHotkey)) {
      currentHotkey = e;
      log('hotkey taken, fell back to: ' + e);
      if (!quiet) {
        showResult({
          title: '快捷键被占用',
          message: '「' + spec + '」已被其它程序占用，已自动改用「' + alt + '」。\n'
            + '想改回原键，可以在「设置 → 快捷键」里重新设置。',
          kind: 'warn',
        });
      }
      return { ok: true, hotkey: e, fallback: alt };
    }
  }
  log('cannot register hotkey: ' + spec);
  return { ok: false, hotkey: null };
}

function onHotkey() {
  log('hotkey pressed');
  startCapture();
}

// ---------------------------------------------------------------- 托盘

function trayIcon() {
  const p = path.join(ASSETS, 'tray.png');
  if (fs.existsSync(p)) return nativeImage.createFromPath(p);
  return nativeImage.createEmpty();
}

function createTray() {
  if (tray && !tray.isDestroyed()) tray.destroy();
  tray = new Tray(trayIcon());
  refreshTrayMenu();
  tray.on('click', () => startCapture());
  tray.on('double-click', () => openSettings());
}

function refreshTrayMenu() {
  if (!tray) return;
  const menu = Menu.buildFromTemplate([
    { label: '截图取字  (' + (currentHotkey || cfg.hotkey) + ')', click: () => startCapture() },
    { type: 'separator' },
    { label: '设置...', click: () => openSettings() },
    { label: '板子地址：' + (cfg.server || '未设置'), enabled: false },
    { label: '重新查找板子...', click: async () => {
      const hit = await discoverServer();
      refreshTrayMenu();
      showResult({
        title: hit ? '找到板子' : '没找到板子',
        message: hit ? '板子地址：' + hit : '没找到板子服务，请确认板子已开机、网线已插。',
        kind: hit ? 'info' : 'warn',
      });
    } },
    { type: 'separator' },
    { label: '退出', click: () => { app.quit(); } },
  ]);
  tray.setContextMenu(menu);
  tray.setToolTip('3588-ocr 截图取字' + (cfg.server ? '  (' + cfg.server + ')' : ''));
}

// ---------------------------------------------------------------- 悬浮工具条

function createToolbar() {
  if (toolbarWin && !toolbarWin.isDestroyed()) {
    toolbarWin.destroy();
    toolbarWin = null;
  }
  if (!cfg.show_toolbar) return;
  const d = screen.getPrimaryDisplay();
  const w = 232;
  const h = 56;
  toolbarWin = new BrowserWindow({
    x: d.bounds.x + d.bounds.width - w - 24,
    y: d.bounds.y + Math.round(d.bounds.height / 2) - h / 2,
    width: w,
    height: h,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: false,
    show: false,
    icon: ICON_PNG,
    webPreferences: { preload: path.join(HERE, 'preload.js'), contextIsolation: true },
  });
  toolbarWin.loadFile(path.join(RENDERER, 'toolbar.html'));
  toolbarWin.webContents.once('did-finish-load', () => {
    toolbarWin.show();
    log('toolbar shown at ' + JSON.stringify(toolbarWin.getBounds())
      + ' visible=' + toolbarWin.isVisible());
  });
  toolbarWin.once('ready-to-show', () => toolbarWin.show());
}

// ---------------------------------------------------------------- 选区遮罩

/** 清理本次抓屏没用到的旧临时图（文件名带 token） */
function cleanShots(keepToken) {
  let names = [];
  try { names = fs.readdirSync(SHOT_DIR); } catch (e) { return; }
  for (const n of names) {
    if (!n.startsWith('shot-') || !n.endsWith('.png')) continue;
    if (keepToken && n.indexOf(keepToken) >= 0) continue;
    try { fs.unlinkSync(path.join(SHOT_DIR, n)); } catch (e) { /* ignore */ }
  }
}

/** 每块屏取一次，保证缩略图是 1:1 物理像素 */
async function captureDisplays(token) {
  const displays = screen.getAllDisplays();
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const t0 = Date.now();

  // 每块屏各取一次，保证缩略图是 1:1 物理像素。
  // 关键是这几次 getSources 必须【并行】发：串行的话双屏要等两倍时间，
  // 用户按完热键要干等两秒才看到选区界面，于是会忍不住连按 —— 那正是
  // v1.2.0 并发抓屏 bug 的触发条件。
  const grabbed = await Promise.all(displays.map(async (d) => {
    const pw = Math.max(1, Math.round(d.bounds.width * d.scaleFactor));
    const ph = Math.max(1, Math.round(d.bounds.height * d.scaleFactor));
    const t1 = Date.now();
    const sources = await desktopCapturer.getSources({
      types: ['screen'],
      thumbnailSize: { width: pw, height: ph },
      fetchWindowIcons: false,
    });
    const t2 = Date.now();
    let src = sources.find((s) => String(s.display_id) === String(d.id));
    if (!src) src = sources.find((s) => s.thumbnail && !s.thumbnail.isEmpty());
    if (!src) return null;
    const buf = src.thumbnail.toPNG();
    return { display: d, buf, getMs: t2 - t1, pngMs: Date.now() - t2 };
  }));

  const shots = [];
  for (const g of grabbed) {
    if (!g) continue;
    // 文件名带本次抓屏的 token：万一还是并发跑起来了，也不会互相覆盖
    const file = path.join(SHOT_DIR, 'shot-' + token + '-' + g.display.id + '.png');
    fs.writeFileSync(file, g.buf);
    shots.push({ display: g.display, file });
    log('  grab display ' + g.display.id + ': getSources ' + g.getMs
      + 'ms, png ' + g.pngMs + 'ms');
  }
  log('capture displays done in ' + (Date.now() - t0) + 'ms');
  cleanShots(token);
  return shots;
}

function rememberForeground() {
  // 记录截图前的前台窗口句柄，供「自动粘贴」时把焦点还回去
  execFile('powershell', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', FOCUS_PS, '-Mode', 'capture'], { timeout: 5000 }, (err, stdout) => {
    if (!err) {
      const h = parseInt(String(stdout).trim(), 10);
      if (!Number.isNaN(h) && h > 0) lastFgHwnd = h;
    }
  });
}

/** 选区期间注册的系统级 Esc：即使遮罩窗口丢了键盘焦点也能取消 */
let escShortcut = null;

function armEscShortcut() {
  if (escShortcut) return;
  try {
    escShortcut = globalShortcut.register('Escape', () => {
      if (overlays.size || overlayWins.size) {
        log('esc pressed -> cancel selection');
        closeOverlays('global-esc');
      }
    });
    if (!escShortcut) log('warn: global Escape shortcut not registered');
  } catch (e) {
    log('warn: global Escape shortcut failed: ' + e);
  }
}

function disarmEscShortcut() {
  if (!escShortcut) return;
  try { globalShortcut.unregister('Escape'); } catch (e) { /* ignore */ }
  escShortcut = null;
}

async function startCapture() {
  // 抓屏本身要 2 秒（双屏各抓一次），这段时间里连按热键会并发跑出多套遮罩，
  // 多出来的窗口从 overlays Map 里掉出去、变成关不掉的野窗口 —— 十字光标就卡死了。
  if (capturing) {
    log('capture ignored: a capture is already in progress');
    return;
  }
  if (overlays.size || overlayWins.size) {
    // 选区中再按一次热键 = 放弃当前选区、重新抓一次屏。
    // 这既是「想换个区域重选」，也是遮罩卡住时的兜底出口。
    log('capture restarted: closing current selection first');
    closeOverlays('restart');
  }
  capturing = true;
  const token = Date.now() + '-' + Math.random().toString(36).slice(2, 8);
  log('capture start, displays=' + screen.getAllDisplays().length);
  log('displays: ' + JSON.stringify(screen.getAllDisplays().map((d) => ({
    id: d.id, bounds: d.bounds, scale: d.scaleFactor, rot: d.rotation,
    primary: d.id === screen.getPrimaryDisplay().id,
  }))));
  rememberForeground();
  let shots;
  try {
    shots = await captureDisplays(token);
  } catch (e) {
    log('capture failed: ' + e);
    showResult({ title: '截图失败', message: String(e), kind: 'error' });
    capturing = false;
    return;
  }
  capturing = false;
  if (!shots.length) {
    log('capture produced no image');
    showResult({ title: '截图失败', message: '没有取到屏幕画面。', kind: 'error' });
    return;
  }
  log('captured ' + shots.length + ' display(s)');
  closeOverlays('stale');   // 万一还有残留，先清干净
  for (const s of shots) {
    const id = String(s.display.id);
    const win = new BrowserWindow({
      x: s.display.bounds.x,
      y: s.display.bounds.y,
      width: s.display.bounds.width,
      height: s.display.bounds.height,
      frame: false,
      transparent: true,
      hasShadow: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      show: false,
      enableLargerThanScreen: true,
      icon: ICON_PNG,
      webPreferences: {
        preload: path.join(HERE, 'preload.js'),
        contextIsolation: true,
        backgroundThrottling: false,
      },
    });
    win.setAlwaysOnTop(true, 'screen-saver');
    win.loadFile(path.join(RENDERER, 'overlay.html'));
    overlays.set(id, win);
    overlayWins.add(win);
    win.webContents.once('did-finish-load', () => {
      if (win.isDestroyed()) return;
      win.webContents.send('overlay:init', {
        displayId: id,
        shot: 'file:///' + s.file.replace(/\\/g, '/'),
        width: s.display.bounds.width,
        height: s.display.bounds.height,
        scaleFactor: s.display.scaleFactor,
        magnifier: !!cfg.magnifier,
      });
      win.show();
    });
    win.on('closed', () => {
      overlayWins.delete(win);
      // 只有 Map 里还指着这个窗口时才删，避免把后来的窗口误删
      if (overlays.get(id) === win) overlays.delete(id);
    });
  }
  armEscShortcut();
  log('overlays up: ' + overlayWins.size + ' window(s)');
  // 全部窗口加载完再让它们接管鼠标
  await new Promise((r) => setTimeout(r, 260));
  for (const [id, win] of overlays) {
    if (win.isDestroyed()) continue;
    win.focus();
    win.webContents.send('overlay:armed', { displayId: id });
  }
}

function closeOverlays(reason) {
  const n = overlayWins.size;
  // 用 overlayWins 全量销毁：只遍历 overlays 会漏掉并发创建出来的野窗口
  for (const win of Array.from(overlayWins)) {
    try { win.destroy(); } catch (e) { /* ignore */ }
  }
  for (const win of Array.from(overlays.values())) {
    try { win.destroy(); } catch (e) { /* ignore */ }
  }
  overlayWins.clear();
  overlays.clear();
  disarmEscShortcut();
  log('overlays closed: ' + n + ' window(s) [' + (reason || '?') + ']');
}

async function onRegionSelected(payload) {
  const { dataUrl, rect, displayId } = payload || {};
  log('region selected: ' + JSON.stringify(rect) + ' bytes=' + (dataUrl ? dataUrl.length : 0));
  closeOverlays('select');
  if (!dataUrl) return;
  let pngBuffer;
  try {
    pngBuffer = Buffer.from(String(dataUrl).split(',')[1], 'base64');
  } catch (e) {
    showResult({ title: '裁剪失败', message: String(e), kind: 'error' });
    return;
  }
  const disp = screen.getAllDisplays().find((d) => String(d.id) === String(displayId));
  const absRect = disp
    ? { x: disp.bounds.x + rect.x, y: disp.bounds.y + rect.y, w: rect.w, h: rect.h }
    : { x: rect.x, y: rect.y, w: rect.w, h: rect.h };
  await runOcr(pngBuffer, absRect);
}

async function runOcr(pngBuffer, absRect) {
  let server = cfg.server;
  if (!await serverHealth(server, 1500)) {
    const hit = await discoverServer();
    if (!hit) {
      showResult({
        title: '没找到板子服务',
        message: '在 ' + (cfg.server || '默认地址') + ' 没找到 npu-ocr 服务。\n\n'
          + '请确认板子已开机、网线已插，然后到「设置」里重新查找。',
        kind: 'warn',
      });
      return;
    }
    server = hit;
  }
  let out;
  try {
    out = await ocrImage(server, pngBuffer);
    log('ocr done: ' + out.lines.length + ' lines in ' + out.elapsed.toFixed(2) + 's');
  } catch (e) {
    log('ocr failed: ' + e);
    showResult({ title: '识别失败', message: String(e), kind: 'error' });
    return;
  }
  lastResult = { ...out, rect: absRect };
  log('ocr text: ' + JSON.stringify(out.text.slice(0, 120)));
  if (cfg.auto_copy && out.text) {
    clipboard.writeText(out.text);
    log('copied to clipboard (' + out.text.length + ' chars)');
  }
  showResultPanel(out, absRect);
  if (cfg.auto_paste && out.text) {
    setTimeout(() => pasteToForeground(out.text), 150);
  }
}

function pasteToForeground(text) {
  clipboard.writeText(text);
  const args = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', FOCUS_PS, '-Mode', 'paste'];
  if (lastFgHwnd) args.push('-Hwnd', String(lastFgHwnd));
  execFile('powershell', args, { timeout: 9000 }, (err) => {
    if (err) log('paste failed: ' + err);
  });
}

// ---------------------------------------------------------------- 结果面板

function showResultPanel(out, absRect) {
  const payload = {
    text: postProcess(out.text),
    lines: out.lines.length,
    elapsed: out.elapsed,
    rect: absRect,
  };
  if (!resultWin || resultWin.isDestroyed()) {
    resultWin = new BrowserWindow({
      width: 520,
      height: 420,
      frame: false,
      transparent: true,
      resizable: true,
      alwaysOnTop: true,
      hasShadow: false,
      minWidth: 360,
      minHeight: 240,
      icon: ICON_PNG,
      webPreferences: {
        preload: path.join(HERE, 'preload.js'),
        contextIsolation: true,
      },
    });
    resultWin.loadFile(path.join(RENDERER, 'result.html'));
    resultWin.webContents.once('did-finish-load', () => {
      resultWin.webContents.send('result:data', payload);
      resultWin.show();
      resultWin.focus();
      placeNear(resultWin, absRect);
    });
    resultWin.on('closed', () => { resultWin = null; });
  } else {
    resultWin.webContents.send('result:data', payload);
    resultWin.show();
    resultWin.focus();
    placeNear(resultWin, absRect);
  }
}

function placeNear(win, rect) {
  try {
    const d = screen.getDisplayMatching({
      x: rect.x, y: rect.y, width: rect.w, height: rect.h,
    });
    const b = d.bounds;
    const w = win.getBounds().width;
    const h = win.getBounds().height;
    let x = rect.x + 20;
    let y = rect.y + 20;
    if (x + w > b.x + b.width) x = rect.x - w - 20;
    if (y + h > b.y + b.height) y = rect.y - h - 20;
    x = Math.max(b.x, Math.min(x, b.x + b.width - 120));
    y = Math.max(b.y, Math.min(y, b.y + b.height - 80));
    win.setPosition(Math.round(x), Math.round(y));
  } catch (e) { /* ignore */ }
}

/** 轻量提示：结果面板已在就用它，否则走原生弹窗 */
function showResult({ title, message, kind }) {
  if (resultWin && !resultWin.isDestroyed() && !resultWin.webContents.isLoading()) {
    resultWin.webContents.send('result:notice', { title, message, kind });
    resultWin.show();
    resultWin.focus();
    return;
  }
  const { dialog } = require('electron');
  dialog.showMessageBox({ type: kind === 'error' ? 'error' : 'info', title, message });
}

// ---------------------------------------------------------------- 设置窗口

function openSettings() {
  if (settingsWin && !settingsWin.isDestroyed()) {
    settingsWin.show();
    settingsWin.focus();
    return;
  }
  settingsWin = new BrowserWindow({
    width: 880,
    height: 600,
    minWidth: 720,
    minHeight: 480,
    frame: false,
    backgroundColor: '#f4f5f7',
    show: false,
    icon: ICON_PNG,
    webPreferences: {
      preload: path.join(HERE, 'preload.js'),
      contextIsolation: true,
    },
  });
  settingsWin.loadFile(path.join(RENDERER, 'settings.html'));
  settingsWin.once('ready-to-show', () => {
    settingsWin.show();
    settingsWin.focus();
  });
  settingsWin.on('closed', () => { settingsWin = null; });
}

// ---------------------------------------------------------------- IPC

ipcMain.handle('cfg:get', () => cfg);
ipcMain.handle('cfg:defaults', () => DEFAULT_CFG);
ipcMain.handle('cfg:save', (_e, patch) => {
  const before = { ...cfg };
  const next = saveConfig(patch);
  if (before.hotkey !== next.hotkey) registerHotkey(next.hotkey, true);
  if (before.show_toolbar !== next.show_toolbar) createToolbar();
  refreshTrayMenu();
  return cfg;
});
ipcMain.handle('cfg:test', async (_e, url) => {
  const h = await serverHealth(url, 5000);
  return h
    ? { ok: true, message: '连接成功 · ' + (h.det || 'npu-ocr') + ' · ' + (h.workers || '?') + ' 核' }
    : { ok: false, message: '连不上（地址不对、板子没开机或服务没起来）' };
});
ipcMain.handle('cfg:discover', async () => {
  const hit = await discoverServer();
  refreshTrayMenu();
  return hit ? { ok: true, server: hit } : { ok: false, server: cfg.server };
});
ipcMain.handle('cfg:reveal', () => { shell.showItemInFolder(CONFIG_PATH); });
ipcMain.handle('cfg:open-repo', () => {
  shell.openExternal('https://github.com/gcaiso9002/3588-ocr');
});

ipcMain.handle('clip:copy', (_e, text) => {
  clipboard.writeText(String(text || ''));
  return true;
});
ipcMain.handle('clip:paste', (_e, text) => {
  clipboard.writeText(String(text || ''));
  pasteToForeground(text);
  return true;
});
ipcMain.handle('clip:read', () => clipboard.readText());

ipcMain.on('result:rescan', () => {
  if (!lastResult) return;
  showResultPanel(lastResult, lastResult.rect || { x: 100, y: 100, w: 100, h: 100 });
});
ipcMain.on('result:close', () => { if (resultWin) resultWin.hide(); });
ipcMain.on('result:open-settings', () => openSettings());

ipcMain.on('overlay:select', (_e, payload) => { onRegionSelected(payload); });
ipcMain.on('overlay:cancel', () => { closeOverlays('cancel'); });

ipcMain.on('toolbar:capture', () => startCapture());
ipcMain.on('toolbar:settings', () => openSettings());
ipcMain.on('app:quit', () => app.quit());
ipcMain.on('win:close', (e) => {
  e.preventDefault();
  const win = BrowserWindow.fromWebContents(e.sender);
  if (win) win.hide();
});
ipcMain.on('win:minimize', (e) => {
  e.preventDefault();
  const win = BrowserWindow.fromWebContents(e.sender);
  if (win) win.minimize();
});

// ---------------------------------------------------------------- 文本后处理

function postProcess(text) {
  let t = String(text || '');
  if (cfg.merge_lines) t = t.replace(/\n+/g, '');
  if (cfg.trim_spaces) t = t.split('\n').map((s) => s.trim()).join('\n');
  return t;
}

// ---------------------------------------------------------------- 启动

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (settingsWin) { settingsWin.show(); settingsWin.focus(); }
    else startCapture();
  });

  app.whenReady().then(async () => {
    nativeTheme.themeSource = 'light';
    // 把渲染层的 console 转发到日志，排查用
    app.on('web-contents-created', (_e, contents) => {
      contents.on('console-message', (_ev, level, message) => {
        if (level >= 1) log('[renderer] ' + message);
      });
      contents.on('did-fail-load', (_ev, code, desc) => {
        log('[renderer] did-fail-load ' + code + ' ' + desc);
      });
    });
    // Windows 上必须注册这个，否则 desktopCapturer 可能拿到黑屏
    session.defaultSession.setDisplayMediaRequestHandler((request, callback) => {
      desktopCapturer.getSources({ types: ['screen'] }).then((sources) => {
        callback({ video: sources[0] });
      }).catch(() => { /* ignore */ });
    }, { useSystemPicker: false });

    loadConfig();
    fs.mkdirSync(SHOT_DIR, { recursive: true });
    createTray();
    registerHotkey(cfg.hotkey, false);   // 被占用时明确告诉用户换成了哪个键
    createToolbar();

    // 自愈：定期扫一遍，把任何「不在册」的选区窗口直接销毁。
    // 野窗口一旦漏网就会一直盖在屏幕上层，十字光标卡死且 Esc 也关不掉。
    setInterval(() => {
      for (const w of BrowserWindow.getAllWindows()) {
        if (w.isDestroyed()) continue;
        if (w.getTitle() !== '选区') continue;
        if (overlayWins.has(w)) continue;
        log('self-heal: destroying orphan overlay window');
        try { w.destroy(); } catch (e) { /* ignore */ }
      }
    }, 2000);

    // 后台预取：先把板子找出来
    discoverServer().then((hit) => {
      refreshTrayMenu();
      if (hit) log('board ready: ' + hit);
    });

    // 命令行：--settings 直接打开设置，--shot 直接进截图
    const argv = process.argv.slice(1);
    if (argv.includes('--settings')) setTimeout(openSettings, 500);
    if (argv.includes('--shot')) setTimeout(startCapture, 500);

    app.on('activate', () => { /* 托盘常驻 */ });
  });

  app.on('window-all-closed', (e) => {
    e.preventDefault();     // 常驻托盘，不退出
  });

  app.on('before-quit', () => {
    globalShortcut.unregisterAll();
  });
}

// ---------------------------------------------------------------- 测试口
//
// 暴露给无 GUI 的回归测试（test_capture_concurrency.js）：
// 它用假的 electron 模块加载本文件，在毫秒级反复压测「抓屏期间重复触发」
// 这条并发路径，全程不弹窗、不占屏幕。
// 正常运行时这些导出没人用。
module.exports = {
  startCapture,
  closeOverlays,
  /** 当前内部状态，给测试断言用 */
  __state: () => ({
    overlays: overlays.size,
    overlayWins: overlayWins.size,
    capturing,
    escArmed: !!escShortcut,
  }),
};

