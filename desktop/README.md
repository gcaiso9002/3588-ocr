# 电脑端（Windows）

全局快捷键截图取字 + HTTP API 客户端。识别在 RK3588 板子上跑。

| 版本 | 说明 |
|---|---|
| **Electron 版（主推）** | `electron/` 目录。圆角悬浮结果面板 + 侧边栏设置界面，仿天若 OCR。→ [../docs/08-Electron桌面端.md](../docs/08-Electron桌面端.md) |
| Tk 版（轻量） | 本目录下 `npu_ocr_gui.py` 等。16.8 MB 的那个 exe 就是它。→ [../docs/05-桌面取字工具.md](../docs/05-桌面取字工具.md) |

## Electron 版

```powershell
cd electron
npm install
npm start          # 开发模式
npm run dist       # 产出 dist\3588-ocr.exe（免安装单文件）
```

或直接下载：[Releases](https://github.com/gcaiso9002/3588-ocr/releases)

```powershell
irm https://github.com/gcaiso9002/3588-ocr/releases/latest/download/3588-ocr.exe -o $env:USERPROFILE\Desktop\3588-ocr.exe
```

双击即用，不用装 Python/Node，不写注册表，卸载就是删文件。
板子地址它会自己扫出来（本地 /24 并发探测 + 换 IP 后自动重连）。

## Tk 版（轻量）

| 文件 | 作用 |
|---|---|
| `npu_ocr_gui.py` | 截图取字 GUI（Ctrl+1） |
| `npu_ocr_gui_config.json` | 配置（服务地址、快捷键、自动复制/粘贴等） |
| `npu_ocr_client.py` | 命令行 API 客户端，纯标准库 |
| `make_exe.py` | 打包成免安装单文件 exe |
| `make_icon.py` | 生成应用图标 `3588-ocr.ico` |
| `test_exe.py` | 打包后的端到端自检（真按 Ctrl+1 框选读剪贴板） |
| `test_exe_discover.py` | 自动发现板子的自检 |
| `start_npu_ocr.bat` | 启动器（无控制台窗口） |

### 装依赖

```powershell
pip install pillow pywin32 pystray
```

* `pillow` —— 截图
* `pywin32` —— 剪贴板、前台窗口、发 Ctrl+V
* `pystray` —— 托盘图标（可选，缺了就没有托盘图标）
* `tkinter` —— Python 自带

### 跑

```powershell
python npu_ocr_gui.py
```

按 **Ctrl+1** 框选 → 出字 → 进剪贴板。

### 改配置

`npu_ocr_gui_config.json`：

```json
{
  "server": "http://192.168.31.230:8766",
  "server_candidates": ["http://192.168.31.40:8766"],
  "hotkey": "ctrl+1",
  "auto_copy": true,
  "auto_paste": false,
  "magnifier": true,
  "keep_result_window": false
}
```

板子 IP 变了改 `server`；加进 `server_candidates` 则连不上时自动探测。

快捷键被别的程序占了（错误 1409）会自动换并弹窗提示，
也可以在程序的「设置」里改。
