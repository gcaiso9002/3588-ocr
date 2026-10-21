# tools/

自检与辅助脚本。

| 文件 | 作用 |
|---|---|
| `test_gui_e2e.py` | 桌面取字工具的端到端测试：置顶一个内容已知的窗口 → 发 Ctrl+1 → 拖框 → 检查剪贴板 |
| `probe_dpi.py` | 量 Tk 事件坐标与物理像素的换算关系（多屏 + 混合 DPI 下用） |
| `remove_baimiao_admin.cmd` | 卸载白描桌面版（右键以管理员身份运行）。它会占用 Ctrl+1 这个全局热键 |

## 跑端到端测试

```powershell
python tools\test_gui_e2e.py
```

需要：

* 板子上的 `npu-ocr.service` 在跑
* `desktop/npu_ocr_gui.py` 与本仓库同目录（或改脚本里的 `APP` 路径）

> 测试脚本自己也要设 DPI aware，否则 `SetCursorPos` 的坐标会被虚拟化，
> 量出来的选区会差 1.5 倍——很容易误判成 app 的 bug。

## 量 DPI 映射

```powershell
python tools\probe_dpi.py
```

会打印：Win32 虚拟桌面范围、Tk 认为的屏幕大小、Tk 窗口的实际物理矩形、
以及每个鼠标事件对应的物理光标位置。用来确认「Tk 事件坐标 = 物理像素 −
虚拟桌面原点」这个前提在别的机器上也成立。
