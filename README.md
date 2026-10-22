# 3588-ocr — 在 RK3588 NPU 上跑 PaddleOCR，并让精度追平 CPU 版

把 PaddleOCR（PP-OCRv4）编译成 RKNN 模型跑在瑞芯微 RK3588 的 **NPU** 上，
并把识别精度做到与 CPU（RapidOCR / ONNXRuntime）**逐字一致**，
再配一个 Windows 端的**快捷键截图取字**小工具——
圆角悬浮结果面板 + 侧边栏设置界面，仿天若 OCR 的观感。

```
电脑                        板子 (RK3588)                 板子 (RK3588)
┌──────────────┐   HTTP    ┌─────────────────┐   RKNN   ┌──────────────┐
│ 截图取字工具 │ ────────► │ npu-ocr.service │ ───────► │  det / cls / │
│ Ctrl+1 框选 │  :8766    │  (常驻, 模型热) │  3 核并行 │  rec  在 NPU │
└──────────────┘           └─────────────────┘          └──────────────┘
```

## 下载

电脑端的截图取字工具是**免安装单文件 exe**，双击就能用：

| 版本 | 大小 | 说明 |
|---|---|---|
| [`3588-ocr.exe`](https://github.com/gcaiso9002/3588-ocr/releases/latest) | ~70 MB | **v1.2.1**：Electron 版，圆角结果面板 + 完整设置界面，仿天若 OCR 的观感；修了「选区遮罩卡死、Esc 按不掉」的 bug |

```powershell
irm https://github.com/gcaiso9002/3588-ocr/releases/latest/download/3588-ocr.exe -o $env:USERPROFILE\Desktop\3588-ocr.exe
```

启动后按 **Ctrl+1**，拖框，文字进剪贴板。
板子的地址它会**自己扫出来**（本地 /24 并发探测 + 换 IP 后自动重连），不用手填。

> 选区界面里：**Esc** 或**右键**取消；再按一次 **Ctrl+1** 会放弃当前选区、重新抓屏。

> 板子端需要自己部署（本仓库只含代码，模型文件太大不入库），部署步骤见下面的「快速开始」。

## 效果

同一套 PP-OCRv4 权重，NPU 与 CPU 逐行 diff：

| 测试图 | NPU 行数 | CPU 行数 | 逐字一致 |
|---|---|---|---|
| `ch_en_num.jpg`（促销图） | 18 | 18 | **18 / 18** |
| `paddle_11.jpg`（成分表） | 16 | 16 | **16 / 16** |
| `article.jpg`（1080×2400 长截图） | 26 | 25 | **23 / 25** |

置信度差 ≤ 0.02。`article.jpg` 那 2 行差异不是识别错——是 NPU 与 ONNXRuntime
的 det 概率图有 ~0.2 的数值差，导致相邻文字块的连通性判断不同（一个把
`测试12345ABC` 连成一块，一个切成两块），**文字本身全对**。

## 速度

| 图片 | NPU（优化前） | NPU（现在） | NPU（走服务） | CPU |
|---|---|---|---|---|
| `ch_en_num.jpg` | 1.09 s | **0.55 s** | **0.44 s** | 2.23 s |
| `paddle_11.jpg` | 1.27 s | **0.54 s** | **0.47 s** | 2.22 s |
| `article.jpg` (1080×2400) | 2.93 s | **1.36 s** | **1.25 s** | 6.39 s |

优化手段见 [docs/03-性能优化.md](docs/03-性能优化.md)：核心是
**RK3588 三个 NPU 核只用了一个**，改成三个实例各钉一核并行后 rec 阶段快 2.2 倍。

## 文档

| 文档 | 内容 |
|---|---|
| [01-项目总览](docs/01-项目总览.md) | 架构、硬件、目录结构、完整部署步骤 |
| [02-精度对齐](docs/02-精度对齐.md) | 把精度追平 CPU 的**三个坑**（末尾 Softmax / cls 拉伸 / det 几何） |
| [03-性能优化](docs/03-性能优化.md) | 三核并行、CTC 解码、det 多核的原理与实测 |
| [04-HTTP-API](docs/04-HTTP-API.md) | 板子上的服务接口 |
| [05-桌面取字工具](docs/05-桌面取字工具.md) | **Windows 端客户端（Electron）**：界面、实现、构建与踩坑，**含「选区遮罩卡死」bug 的复盘** |
| [06-模型构建](docs/06-模型构建.md) | ONNX → RKNN 的完整流程与工具链踩坑 |

## 快速开始

### 1. 板子上部署服务

```bash
git clone https://github.com/gcaiso9002/3588-ocr.git
cd 3588-ocr
scp board/* root@<board>:/opt/npu-ocr/
scp board/npu-ocr.service root@<board>:/etc/systemd/system/
ssh root@<board> 'systemctl daemon-reload && systemctl enable --now npu-ocr.service'
curl http://<board>:8766/health
```

模型文件不在仓库里（太大），按 [docs/06-模型构建.md](docs/06-模型构建.md) 现场编译。

### 2. 电脑上装取字工具

**方式一（推荐）：下载绿色版 exe。** 双击即用，不用装 Python/Node，
不写注册表，卸载就是删文件。

```powershell
irm https://github.com/gcaiso9002/3588-ocr/releases/latest/download/3588-ocr.exe -o $env:USERPROFILE\Desktop\3588-ocr.exe
```

启动后按 **Ctrl+1**，拖框，文字进剪贴板。
板子的地址它会**自己扫出来**（本地 /24 并发探测 + 换 IP 后自动重连），不用手填。

界面：圆角悬浮的结果面板 + 侧边栏设置界面（基本 / 快捷键 / 识别 / 高级 / 关于）。
详见 [docs/05-桌面取字工具.md](docs/05-桌面取字工具.md)。

**方式二：从源码跑。**

```bash
cd desktop/electron
npm install
npm start
```

### 3. 或者直接调 API

```bash
curl -F "file=@pic.jpg" http://<board>:8766/ocr
```

## 目录

```
board/     板子上跑的东西：流水线、HTTP 服务、RKNN 转换脚本、systemd 单元
desktop/   电脑端：Electron 截图取字客户端 + 命令行 API 客户端
docs/      详细文档
samples/   测试图片
tools/     构建与自检脚本
```

## 许可与致谢

本仓库代码以 **Apache-2.0** 发布。

* 前处理与后处理语义参考自 [RapidOCR](https://github.com/RapidAI/RapidOCR)（Apache-2.0）
* 模型权重来自 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 的 PP-OCRv4（Apache-2.0）
* NPU 推理由 [rknn-toolkit2](https://github.com/airockchip/rknn-toolkit2) 提供

> 模型权重遵循上游许可，本仓库不重新分发权重文件。

## 已知的坑（先读这个，能省很多时间）

1. **模型末尾的 Softmax 不能留在 NPU 上**。RKNN 编译器把它编成 fp16 算子
   `exSoftmax13`，fp16 的 `exp()` 精度不够把分布压平，所有置信度从 0.99 掉到
   **0.70**，还偶发错字。解法是用 onnx 剪掉末尾 Softmax/Sigmoid，NPU 只出
   logits，softmax 用 numpy float32 算。
2. **cls 的裁剪图不能拉伸**。PaddleOCR 是「按比例缩到自然宽 + 补边到 192」，
   直接 `resize(crop,(192,48))` 会让 cls 误判 180° 并旋转，rec 读出空串，
   **整行丢失**。
3. **det 的输入几何必须照抄 RapidOCR**（`limit_side_len=736, limit_type=min`，
   32 的倍数，四周不补边）。塞进 960×960 画布补黑边会改变 DB 概率图，
   **整块横幅被吞掉**。

详见 [docs/02-精度对齐.md](docs/02-精度对齐.md)。
