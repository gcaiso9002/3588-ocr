# 电脑端（Windows）

全局快捷键截图取字 + HTTP API 客户端。识别在 RK3588 板子上跑。

| 文件 | 作用 |
|---|---|
| `npu_ocr_gui.py` | 截图取字 GUI（Ctrl+1） |
| `npu_ocr_gui_config.json` | 配置（服务地址、快捷键、自动复制/粘贴等） |
| `npu_ocr_client.py` | 命令行 API 客户端，纯标准库 |
| `start_npu_ocr.bat` | 启动器（无控制台窗口） |

## 装依赖

```powershell
pip install pillow pywin32 pystray
```

* `pillow` —— 截图
* `pywin32` —— 剪贴板、前台窗口、发 Ctrl+V
* `pystray` —— 托盘图标（可选，缺了就没有托盘图标）
* `tkinter` —— Python 自带

## 跑

```powershell
python npu_ocr_gui.py
```

按 **Ctrl+1** 框选 → 出字 → 进剪贴板。

详见 [../docs/05-桌面取字工具.md](../docs/05-桌面取字工具.md)。

## 改配置

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
