# tools/

自检与辅助脚本。

| 文件 | 作用 |
|---|---|
| `check_links.py` | 检查仓库里所有 markdown 相对链接是否还能解析（改完文档跑一下） |
| `remove_baimiao_admin.cmd` | 卸载白描桌面版（右键以管理员身份运行）。它会占用 Ctrl+1 这个全局热键 |

桌面取字客户端自己的端到端测试在 `desktop/electron/` 下：

```powershell
cd desktop\electron
python test_electron.py       # 真按 Ctrl+1 框选 → 检查剪贴板
python test_discover.py       # 把板子地址改成死地址 → 看它能不能自己找回来
```

> 测试脚本自己也要设 DPI aware，否则 `SetCursorPos` 的坐标会被虚拟化，
> 框出来的位置会差 1.5 倍——很容易误判成 app 的 bug。

## 检查文档断链

```powershell
python tools\check_links.py
```
