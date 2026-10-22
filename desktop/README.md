# 电脑端（Windows）

截图取字客户端 + HTTP API 客户端。**识别在 RK3588 板子上跑。**

| 文件 | 作用 |
|---|---|
| `electron/` | **Electron 客户端（唯一版本）**。圆角悬浮结果面板 + 侧边栏设置界面，仿天若 OCR。→ [../docs/05-桌面取字工具.md](../docs/05-桌面取字工具.md) |
| `npu_ocr_client.py` | 命令行 API 客户端，纯标准库（配合板子上的服务用） |

> 之前还有一个 16.8 MB 的 Tk 版（界面是系统原生控件，观感太差），
> v1.2.0 起已整个删除，不再维护。

## Electron 客户端

```powershell
cd electron
npm install
npm start          # 开发模式
npm run dist       # 产出 dist\3588-ocr.exe（免安装单文件）
```

或者直接下载（免安装、双击即用）：

[Releases](https://github.com/gcaiso9002/3588-ocr/releases)

```powershell
irm https://github.com/gcaiso9002/3588-ocr/releases/latest/download/3588-ocr.exe -o $env:USERPROFILE\Desktop\3588-ocr.exe
```

* 双击即用，目标机不用装 Python / Node
* 不写注册表、不写 `Program Files`，卸载 = 删 exe + 删 `%APPDATA%\3588-ocr`
* 板子地址它会**自己扫出来**（本地 /24 并发探测 + 换 IP 后自动重连），不用手填
* 按 **Ctrl+1** 框选 → 出字 → 进剪贴板
* 选区界面里 **Esc / 右键** 取消
* 选区 **2 秒没动静自动撤掉遮罩**（挪一下鼠标就重新计时），可在「设置 → 识别」里改
* 抓屏途中再按一次 **Ctrl+1** 会作废当前这次、重新抓一张新的（最新的按键赢）

## 命令行 API 客户端

```powershell
python npu_ocr_client.py pic.jpg            # 默认板子地址
python npu_ocr_client.py pic.jpg --text     # 只输出纯文本
python npu_ocr_client.py pic.jpg --json     # 输出 JSON
python npu_ocr_client.py pic.jpg --server http://192.168.31.230:8766
```

接口细节见 [../docs/04-HTTP-API.md](../docs/04-HTTP-API.md)。
