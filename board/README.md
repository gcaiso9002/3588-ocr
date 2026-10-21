# 板子端（RK3588）

跑在瑞芯微 RK3588 上的部分：识别流水线、HTTP 服务、RKNN 转换脚本。

## 部署

```bash
# 1. 放代码
scp board/* root@<board>:/opt/npu-ocr/

# 2. 放模型（仓库里没有，见 ../docs/06-模型构建.md）
#    /opt/npu-ocr/models/*.rknn + *.sizes.json + ppocr_keys_v1.txt

# 3. 注册服务
scp board/npu-ocr.service root@<board>:/etc/systemd/system/
ssh root@<board> 'systemctl daemon-reload && systemctl enable --now npu-ocr.service'

# 4. 验证
curl http://<board>:8766/health
```

## 文件

| 文件 | 作用 |
|---|---|
| `npu_ocr.py` | 流水线：det → cls → rec 全在 NPU，后处理在 numpy |
| `npu_ocr_server.py` | HTTP 服务（`:8766`），模型常驻 |
| `conv.py` | 通用 ONNX → RKNN 转换器 |
| `strip_tail.py` | 剪掉模型末尾的 Softmax / Sigmoid（**必须做**） |
| `build_det_ms.py` | 构建多尺寸 det 模型 |
| `compare.py` | 与 CPU 基准逐行 diff |
| `npu-ocr` | CLI 入口，装到 `/usr/local/bin/npu-ocr` |
| `po-ocr` | CPU 基准入口，装到 `/usr/local/bin/po-ocr`（RapidOCR/ORT） |

## 命令行

```bash
npu-ocr  <image> [...]        # text\t(score) 每行一条
npu-ocr --json <image>       # 完整 JSON
npu-ocr --warmup             # 只加载模型

po-ocr  <image> [...]        # CPU 基准（RapidOCR / ONNXRuntime）

python3 compare.py /opt/npu-ocr/ch_en_num.jpg   # 与 CPU 逐行 diff
```

## 环境变量

见 [../docs/01-项目总览.md](../docs/01-项目总览.md#环境变量开关)。

## 注意

* 转换用的 venv 在 `/opt/rknnconv/`，运行时用系统 python3（装了 rknnlite）
* `compare.py` 的基准 JSON 用**绝对路径**做 key，传相对路径会显示
  `CPU=0 lines` 全判 DIFF（假失败）
* 模型目录里 `*.sizes.json` 不能删——运行时靠它把输入吸附到受支持尺寸
