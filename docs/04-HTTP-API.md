# 04 · HTTP API

板子上的 `npu-ocr.service` 提供一个常驻的 OCR HTTP 服务，模型一直热着，
每个请求只付推理时间。

```
http://<board>:8766
```

systemd 单元 `npu-ocr.service`，`systemctl enable` 过，开机自启。

---

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 服务状态、worker 数、模型名 |
| `GET` | `/` | 浏览器拖拽测试页（拖图片进去就出文字） |
| `POST` | `/ocr` | 识别图片 |
| `GET` | `/ocr?path=板上路径` | 识别板子上已有的文件 |
| `POST` | `/ocr?path=板上路径` | 同上 |

### 三种发图方式

```bash
# 1) multipart/form-data（浏览器和大多数客户端都这么发）
curl -F "file=@C:/Users/me/pic.jpg" http://<board>:8766/ocr

# 2) raw body
curl --data-binary @pic.jpg http://<board>:8766/ocr

# 3) 板上的文件
curl "http://<board>:8766/ocr?path=/opt/npu-ocr/article.jpg"
```

一次请求可以带**多个 `file` 字段**，按顺序处理完返回 JSON 数组。

### 查询参数

| 参数 | 作用 |
|---|---|
| `text=1` | 返回纯文本（`text/plain`）而不是 JSON |
| `box=0` | JSON 里不带坐标框 |

### 响应

```json
{
  "image": "article.jpg",
  "count": 26,
  "elapsed": 1.26,
  "timing": {"det": 0.55, "rec": 0.68, "total": 1.26},
  "lines": [
    {"text": "推荐写留言", "score": 0.9995,
     "box": [[822,2294],[1022,2294],[1022,2331],[822,2331]]}
  ]
}
```

`box` 是原图像素坐标，四点顺序为 左上 → 右上 → 右下 → 左下。

### 错误

| 状态码 | 情况 |
|---|---|
| 400 | 图解不开 / 没有文件部分 / body 为空 |
| 404 | `path=` 指向的文件不存在 |
| 413 | 超过 32 MB |
| 500 | 识别过程异常（返回 `{"error": "..."}`） |

---

## CORS

已放开（`Access-Control-Allow-Origin: *`），所以电脑上的网页可以直接调。

---

## 电脑端客户端

`desktop/npu_ocr_client.py`，**只用标准库**：

```powershell
python npu_ocr_client.py pic.jpg                 # 文字 + 置信度
python npu_ocr_client.py --text pic.jpg > o.txt  # 只要纯文本，可重定向
python nppu_ocr_client.py --json pic.jpg          # 原始 JSON
python npu_ocr_client.py --boxes pic.jpg          # 带坐标框
python npu_ocr_client.py --server http://<board>:8766 pic.jpg
```

> Windows 控制台默认 GBK，会把中文显示成乱码。脚本里已经
> `sys.stdout.reconfigure(encoding="utf-8")` 强制 UTF-8。
> 状态行不写 stderr，免得 PowerShell 当成错误弹 `NativeCommandError`。

---

## 实测延迟（电脑 → 板子）

| 图片 | 往返 | 板子侧推理 |
|---|---|---|
| `ch_en_num.jpg` | 0.44 ~ 0.65 s | 0.44 s |
| `paddle_11.jpg` | 0.53 s | 0.47 s |
| `article.jpg` (1080×2400) | 1.26 ~ 1.33 s | 1.26 s |

3 张图并发发：2.24 s（服务对请求串行处理，因为流水线本身已经吃满三核）。

---

## 服务端的并发模型

流水线内部用 3 个 worker 抢占 3 个 NPU 核，所以**同一个请求里**已经是满载，
外部请求再并发只会互相抢核。服务端因此用一把锁把请求串行化——
对 NPU 密集型任务来说，排队比抢核更划算。
