# 08 — Electron 桌面端（v1.1.0 起的主力客户端）

原来的 Tk 版（[05-桌面取字工具](05-桌面取字工具.md)）能用，但界面是系统原生控件，
和天若 OCR / 白描那种「小圆角卡片 + 侧边栏设置」的观感差得远。
**v1.1.0 起主推 Electron 版**：界面重做、补上设置界面、打包成免安装单文件。

> 功能没缩水：全局热键、框选、放大镜、自动复制/粘贴、托盘、板子自动发现，全都在。
> 变的只是「壳」——识别依旧在 RK3588 的 NPU 上跑。

---

## 1. 三个界面

| 界面 | 说明 |
|---|---|
| **设置窗口** | 侧边栏导航 + 卡片式分区：基本设置 / 快捷键 / 识别 / 高级 / 关于 |
| **结果面板** | 圆角悬浮卡片，顶部标题栏（行数 · 耗时），中间可编辑文本区，底部操作条 |
| **悬浮工具条** | 常驻屏幕右侧的小胶囊，左边蓝色圆形截图键，右边设置 / 退出，右侧手柄可拖动 |
| 选区遮罩 | 全屏压暗 + 选区高亮 + 十字线 + 放大镜 + 实时尺寸标签 |

设置界面长这样（左边导航，右边分区，底部保存）：

```
┌────────────┬──────────────────────────────────────┐
│ 3588-ocr   │  基本设置                              │
│            │  识别在 RK3588 板子的 NPU 上完成…        │
│ ● 基本设置 │  ┌──────────────────────────────────┐ │
│   快捷键   │  │ 板子服务                         │ │
│   识别     │  │  服务地址 [http://…:8766]        │ │
│   高级     │  │  备用地址 [http://…:8766]        │ │
│   关于     │  │  [测试连接] [自动查找板子]  ● 在线 │ │
│            │  └──────────────────────────────────┘ │
│ ● 板子在线 │  ┌──────────────────────────────────┐ │
│  192.168…  │  │ 行为  ☑自动复制 ☐自动粘贴 …      │ │
└────────────┴──────────────────────────────────────┘
```

---

## 2. 目录结构

```
desktop/electron/
├── main.js              主进程：托盘 / 热键 / 选区 / 结果面板 / 设置窗口
├── preload.js           contextBridge 暴露的安全 API
├── renderer/
│   ├── overlay.*        选区遮罩（每屏一个窗口）
│   ├── result.*         结果面板
│   ├── settings.*       设置窗口
│   └── toolbar.*        悬浮工具条
├── assets/              图标（icon.ico / icon.png / tray.png）
├── native/focus.ps1     取前台窗口句柄 / 模拟 Ctrl+V
├── make_assets.py       生成图标
├── test_electron.py     端到端自检
└── package.json         含 electron-builder 配置
```

---

## 3. 关键实现

### 3.1 每块屏一个遮罩窗口，而不是一个大窗口

Electron 的 `screen` API 返回的是**每块屏各自的 DIP**（主屏 3840×2160@150% → 2560×1440 DIP，
副屏 1080×1920@100% → 1080×1920 DIP）。

如果做一个横跨虚拟桌面的大窗口，Windows 只会给这个窗口**一个 DPI 上下文**，
副屏那部分就会整体缩放错位，选区框和实际内容对不上。

所以本实现是**每块屏开一个遮罩窗口**，窗口尺寸正好等于该屏的 DIP 尺寸：

```js
new BrowserWindow({
  x: s.display.bounds.x, y: s.display.bounds.y,
  width: s.display.bounds.width, height: s.display.bounds.height,
  frame: false, transparent: true, alwaysOnTop: true, skipTaskbar: true,
});
```

每块屏内部都是 1:1 精确的，代价是**选区不能跨屏**（在哪块屏按下就只在那块屏里选）。

### 3.2 裁剪在渲染层用 canvas 做，不在主进程做

遮罩窗口里的 `<canvas>` 后端缓冲是**物理像素**（`bounds.width * scaleFactor`），
显示尺寸是 DIP。选区确定后直接从画布上按 1:1 裁出来：

```js
const s = state.scaleFactor;
const sx = Math.round(r.x * s), sy = Math.round(r.y * s);
const sw = Math.round(r.w * s), sh = Math.round(r.h * s);
ctx.drawImage(shot, sx, sy, sw, sh, 0, 0, sw, sh);
```

好处：主进程完全不用关心 DPI 换算，裁剪出来的一定是**用户在屏幕上框的那几个物理像素**。

实测校准：鼠标拖 `(458,458)→(1300,834)`（物理），应用记录
`{x:305, y:305, w:561, h:251}`（DIP），×1.5 还原 = `(458,458,842,377)`，**像素级一致**。

### 3.3 抓屏要 1:1，每块屏单独取一次

`desktopCapturer.getSources()` 的 `thumbnailSize` 对所有屏是同一个值，
给一个「最大屏」的尺寸会让别的屏被等比缩放。所以**每块屏单独调一次**：

```js
const pw = Math.round(d.bounds.width * d.scaleFactor);
const sources = await desktopCapturer.getSources({
  types: ['screen'], thumbnailSize: { width: pw, height: ph },
});
```

再按 `source.display_id` 匹配回对应的 display。

### 3.4 Windows 上必须注册屏幕捕获的 handler

不注册的话 `desktopCapturer` 很可能拿到**黑屏**：

```js
session.defaultSession.setDisplayMediaRequestHandler((request, callback) => {
  desktopCapturer.getSources({ types: ['screen'] }).then((sources) => {
    callback({ video: sources[0] });
  });
}, { useSystemPicker: false });
```

### 3.5 全局热键

Electron 的 `globalShortcut` 直接可用，不用像 Tk 版那样自己 ctypes 调 `RegisterHotKey`：

```js
globalShortcut.register('CommandOrControl+1', onHotkey);
```

`ctrl+1` → `CommandOrControl+1`（`CommandOrControl` 在 Windows 上就是 Ctrl）。

**热键被占用时自动降级**：注册失败就按修饰键不变、换一批键名（`2 3 4 … q w e r …`）
再试，并把**实际生效的键**明确告诉用户（弹窗 + 托盘菜单 + 日志）。
配置里的原键不会被改写，用户随时能在「设置 → 快捷键」里改回来。

设置界面里的快捷键输入框支持**直接按下组合键录制**（点一下输入框，按 Ctrl+Shift+A，松开即记录）。

### 3.6 模拟粘贴：一次 PowerShell 调用

要把焦点还给截图前的窗口，需要 Win32 的 `SetForegroundWindow` + 模拟按键。
不引原生模块（免去编译和 ABI 问题），用一段 PowerShell：

```powershell
# native/focus.ps1
#   -Mode capture        打印当前前台窗口句柄
#   -Mode paste -Hwnd N 把焦点还给 N 号窗口并发送 Ctrl+V
```

`SetForegroundWindow` 有「前台锁」限制，所以脚本里用 `AttachThreadInput`
挂到前台线程再调用，否则大概率失败。粘贴前先把识别结果写进剪贴板，再发 Ctrl+V。

### 3.7 配置与「绿色」

- 配置：`%APPDATA%\3588-ocr\config.json`
- 日志：`%APPDATA%\3588-ocr\3588-ocr.log`
- 不写注册表、不写 `Program Files`、卸载 = 删 exe + 删 `%APPDATA%\3588-ocr`

---

## 4. 自己构建

```powershell
cd desktop/electron
npm install
npm start          # 开发模式
npm run dist       # 产出 dist\3588-ocr.exe（免安装单文件）
```

打包用 `electron-builder` 的 **portable** 目标，产出单个 exe。

### 踩过的坑

1. **必须 `npm run dist` 而不是手敲 electron-builder**，否则 `files` 过滤会漏。
2. **下载 electron 二进制要配镜像**，国内直连 GitHub 经常断：

   ```powershell
   $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
   $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
   ```

3. **透明窗口的 `ready-to-show` 不一定触发**，工具条用这个事件显示会一直不出现。
   统一改成 `webContents.once('did-finish-load', () => win.show())`。
4. **窗口建了不 `show()` 就等于没有**——遮罩层踩过一次，选区完全无响应。
5. `web-contents-created` 里挂 `console-message` 转发行内日志，排查渲染层问题非常好用。
6. **JavaScript 没有 printf 风格的 `%` 占位符！**

   ```js
   // ❌ 这是 Python 写法，在 JS 里会拼出 "http://NaN.NaN.NaN.5:8766"
   'http://%s.%s.%s.%d:%d' % (a, b, c, i, port)
   // ✅ 必须用模板字符串
   `http://${a}.${b}.${c}.${i}:${port}`
   ```

   这个坑让「自动扫描网段找板子」**静默失效**：所有 URL 都是非法的，
   扫完 254 个地址耗时 28ms 却一个没命中，日志里只有一句「没找到」。
   教训：扫网段这种「失败了也不报错」的功能，必须**用假地址测一次**才靠谱。
7. **取本机 IP 不能靠枚举网卡**——机器上有虚拟网卡（VMware / Hyper-V / WSL），
   枚举出来的第一个 IPv4 往往是虚拟网段的，扫错地方。
   要用 **UDP connect 让系统按路由表选源地址**（不发包）：

   ```js
   const sock = dgram.createSocket('udp4');
   sock.connect(80, '8.8.8.8', () => resolve(sock.address().address));
   ```

8. **配置文件带 BOM 会让 `JSON.parse` 直接抛异常**，应用静默退回默认值
   （Windows PowerShell 5.1 的 `Set-Content -Encoding UTF8` 就会写 BOM）。
   读的时候要 `.replace(/^\uFEFF/, '')`。
9. **electron-builder 打包时解 winCodeSign 会失败**：7-Zip 要给两个 macOS 的
   `.dylib` 建符号链接，Windows 没那个权限就报
   `Cannot create symbolic link : 客户端没有所需的特权`。
   解法：自己把 `winCodeSign-2.6.0.7z` 用**不带 `-snld`** 的 7za 解到
   `%LOCALAPPDATA%\electron-builder\Cache\winCodeSign\winCodeSign-2.6.0\`，
   符号链接降级成普通文件（Windows 用不到 macOS 的 dylib），
   electron-builder 就会直接用缓存，不再下载解压。

---

## 5. 端到端自检

`test_electron.py` 会真的做一遍完整流程：

1. 建一个置顶的已知文本窗口（`Hello 12345 ABCdef` / `识别测试 67890`）；
2. 用 `keybd_event` 模拟 **Ctrl+1**；
3. 用 `SetCursorPos` + `mouse_event` 真的拖一个框；
4. 读回剪贴板，校验 `12345` / `ABCdef` / `识别测试` 都在。

```powershell
python test_electron.py
```

实测输出：

```
known-text window rect: (418, 418, 1340, 874)
clipboard : '本地\n识别测试 67890\n远程\n网站\n定时\n插件·技能\n12345\nHell0\nABCdef\n新建任务'
RESULT    : PASS
```

自动发现板子的自检：

```powershell
python test_discover.py
```

它会把配置里的板子地址改成一个**没人监听的死地址**、清空候选地址，
再启动 app，然后看它能不能自己扫网段把板子找回来并写回配置。

```
config set to the dead address http://192.168.31.199:8766
config now points at : http://192.168.31.40:8766
log says             : 11:37:38  found an npu-ocr service at http://192.168.31.40:8766
RESULT    : PASS
```

> ⚠️ 驱动脚本本身必须设 DPI aware
> （`SetProcessDpiAwarenessContext(-4)`），否则 `SetCursorPos` 的坐标被虚拟化，
> 框出来的位置会差 1.5 倍。
>
> 另外 `GetWindowRect` 返回的是**物理**坐标，而 Tk 自己 `geometry("+x+y")`
> 用的是 DIP，两者不能混用——测试里让 Tk 自己摆窗口，再用 `GetWindowRect` 反查真实位置。

---

## 6. 和 Tk 版的关系

| | Tk 版（v1.0.0） | Electron 版（v1.1.0） |
|---|---|---|
| 体积 | 16.8 MB | ~100 MB |
| 界面 | 系统原生控件 | 自绘卡片 + 侧边栏设置 |
| 设置界面 | 一个简单对话框 | 五个分区的完整设置 |
| 依赖 | 目标机不用装 Python | 目标机不用装 Node |
| 热键 | ctypes `RegisterHotKey` | `globalShortcut` |
| 裁剪 | 主进程 `nativeImage.crop` | 渲染层 canvas 1:1 |

Tk 版继续保留在 `desktop/`，作为「要小体积、够用就行」的选择。
**日常用 Electron 版。**

---

## 7. 已知限制

1. **选区不能跨屏**（见 3.1），单屏内是像素级精确的。
2. **Electron 版体积约 100 MB**，主要是 Chromium 运行时；识别全在板子上，这里没塞模型。
3. **Windows only**（用到了 Win32 API 和 Electron 的 Windows 行为）。
4. 未做代码签名，SmartScreen 首次运行需点「更多信息 → 仍要运行」。
