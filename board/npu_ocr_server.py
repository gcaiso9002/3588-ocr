#!/usr/bin/env python3
"""Persistent HTTP service for the NPU OCR pipeline (RK3588, port 8766).

The models stay loaded, so a request only pays the inference time
(~0.45 s for a shop sign, ~1.3 s for a 1080x2400 screenshot) instead of
the ~1.9 s cold start of the npu-ocr CLI.

Endpoints
---------
GET  /                       a small browser page: drop an image, get the text
GET  /health                 {"ok": true, "workers": 3, ...}

POST /ocr                    OCR an image.  Three ways to send it:
                                - raw body:            the image bytes
                                - multipart/form-data: a "file" field (and you
                                  may send several "file" fields at once)
                                - ?path=/some/file.jpg  a file on the board
GET  /ocr?path=/some/file.jpg  same, for convenience
POST /ocr?path=/some/file.jpg  same

Query flags
-----------
    text=1     return the plain text instead of the full JSON
    box=0      leave the text boxes out of the JSON

Response
--------
    {"image": "upload", "count": 26, "elapsed": 1.25,
     "timing": {"det": 0.55, "rec": 0.70, "total": 1.25},
     "lines": [{"text": "...", "score": 0.99, "box": [[x,y],[x,y],[x,y],[x,y]]}]}
"""
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/opt/npu-ocr")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

import npu_ocr as N  # noqa: E402

HOST = os.environ.get("OCR_HOST", "0.0.0.0")
PORT = int(os.environ.get("OCR_PORT", "8766"))
MAX_UPLOAD = 32 * 1024 * 1024

_ocr = None
_lock = threading.Lock()   # one pipeline at a time; it already uses 3 cores

PAGE = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>RK3588 NPU OCR</title>
<style>
 body{font:14px/1.6 system-ui,sans-serif;margin:0;background:#111;color:#eee;
      display:flex;flex-direction:column;height:100vh}
 header{padding:10px 16px;background:#1c1c1c;font-weight:600}
 #drop{flex:1;margin:16px;border:2px dashed #444;border-radius:12px;
       display:flex;align-items:center;justify-content:center;text-align:center;
       color:#888;overflow:auto}
 #drop.hot{border-color:#4a9;color:#4ad}
 textarea{width:calc(100% - 32px);height:32vh;margin:0 16px 16px;background:#1c1c1c;
          color:#ddd;border:1px solid #333;border-radius:8px;padding:10px;
          font:13px/1.5 ui-monospace,Consolas,monospace}
 button{background:#2a6;border:0;color:#fff;padding:6px 14px;border-radius:6px;
        cursor:pointer;margin-right:8px}
 .bar{padding:0 16px 8px;color:#888;font-size:12px}
</style></head><body>
<header>RK3588 NPU OCR &mdash; PP-OCRv4 via RKNN</header>
<div class="bar"><button onclick="copy()">复制全部</button>
 <span id="stat">把图片拖进来，或点击选择文件</span></div>
<div id="drop">拖拽图片到这里<br>或 <a href="#" id="pick">选择文件</a></div>
<textarea id="out" spellcheck="false" placeholder="识别结果会出现在这里"></textarea>
<input type="file" id="file" accept="image/*" multiple hidden>
<script>
const drop=document.getElementById('drop'),out=document.getElementById('out'),
      stat=document.getElementById('stat'),file=document.getElementById('file');
document.getElementById('pick').onclick=e=>{e.preventDefault();file.click()};
file.onchange=()=>{if(file.files.length)send([...file.files])};
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,x=>{
  x.preventDefault();drop.classList.add('hot')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,x=>{
  x.preventDefault();drop.classList.remove('hot')}));
drop.addEventListener('drop',x=>{
  const f=[...x.dataTransfer.files].filter(f=>f.type.startsWith('image/'));
  if(f.length)send(f)});
async function send(files){
  const fd=new FormData();
  files.forEach(f=>fd.append('file',f,f.name));
  stat.textContent='识别中... ('+files.length+' 张)';
  const t0=performance.now();
  try{
    const r=await fetch('/ocr',{method:'POST',body:fd});
    const j=await r.json();
    const ms=Math.round(performance.now()-t0);
    if(j.error){stat.textContent='错误: '+j.error;return}
    const txt=(j.lines||[]).map(l=>l.text).join('\n');
    out.value=(out.value?out.value+'\n':'')+txt;
    stat.textContent=(j.lines||[]).length+' 行 / '+ms+' ms'+
      (j.timing?'  (det '+j.timing.det+'s, rec '+j.timing.rec+'s)':'');
  }catch(e){stat.textContent='错误: '+e}
}
function copy(){out.select();document.execCommand('copy');stat.textContent='已复制'}
</script></body></html>"""


def get_ocr():
    global _ocr
    if _ocr is None:
        _ocr = N.NpuOcr()
    return _ocr


def run(img, source, want_box=True):
    ocr = get_ocr()
    with _lock:
        lines, timing = ocr(img)
    if not want_box:
        lines = [{"text": l["text"], "score": l["score"]} for l in lines]
    return {"image": source, "count": len(lines),
            "elapsed": round(timing["total"], 3), "timing": timing, "lines": lines}


def parse_multipart(body, content_type):
    """Return [(filename, bytes)] from a multipart/form-data body."""
    m = re.search(r'boundary=("?)([^";]+)\1', content_type or "")
    if not m:
        return []
    boundary = m.group(2).encode()
    out = []
    for part in body.split(b"--" + boundary)[1:]:
        if part.startswith(b"--"):
            break
        part = part.lstrip(b"\r\n")
        head, _, data = part.partition(b"\r\n\r\n")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        fn = re.search(r'filename="([^"]*)"', head.decode("utf-8", "replace"))
        out.append((fn.group(1) if fn else None, data))
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "npu-ocr/1.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, payload, ctype="application/json; charset=utf-8",
              raw=False):
        body = payload if raw else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"", raw=True)

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8",
                       raw=True)
            return
        if path in ("/health", "/healthz"):
            self._send(200, {"ok": True, "workers": N.NPU_WORKERS,
                             "det": os.path.basename(N.DET_RKNN),
                             "rec": os.path.basename(N.REC_RKNN),
                             "uptime": round(time.time() - _T0, 1)})
            return
        if path == "/ocr":
            src = params.get("path", "")
            if not src:
                self._send(400, {"error": "need ?path= or an image body"})
                return
            self._ocr_path(src, params)
            return
        self._send(404, {"error": "not found"})

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path != "/ocr":
            self._send(404, {"error": "not found"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            self._send(400, {"error": "empty body"})
            return
        if n > MAX_UPLOAD:
            self._send(413, {"error": "image too large (max 32 MB)"})
            return
        body = self.rfile.read(n)
        ctype = self.headers.get("Content-Type", "")

        src = params.get("path", "")
        if src:
            self._ocr_path(src, params)
            return

        if "multipart/form-data" in ctype:
            files = parse_multipart(body, ctype)
            if not files:
                self._send(400, {"error": "no file part in the body"})
                return
            results = []
            for name, data in files:
                img = cv2.imdecode(np.frombuffer(data, np.uint8),
                                   cv2.IMREAD_COLOR)
                if img is None:
                    results.append({"image": name, "error": "cannot decode"})
                    continue
                try:
                    results.append(run(img, name or "upload",
                                       params.get("box", "1") != "0"))
                except Exception as e:  # noqa: BLE001
                    results.append({"image": name, "error": repr(e)})
            self._reply(results, params)
            return

        img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            self._send(400, {"error": "cannot decode image"})
            return
        try:
            self._reply(run(img, "upload", params.get("box", "1") != "0"), params)
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": repr(e)})

    # -------------------------------------------------------------- helpers
    def _reply(self, result, params):
        if params.get("text") == "1":
            items = result if isinstance(result, list) else [result]
            txt = "\n".join(l["text"] for it in items for l in it.get("lines", []))
            self._send(200, txt.encode("utf-8"), "text/plain; charset=utf-8",
                       raw=True)
            return
        self._send(200, result)

    def _ocr_path(self, src, params):
        if not os.path.exists(src):
            self._send(404, {"error": "no such file: %s" % src})
            return
        img = cv2.imread(src)
        if img is None:
            self._send(400, {"error": "cannot read image"})
            return
        try:
            self._reply(run(img, src, params.get("box", "1") != "0"), params)
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": repr(e)})


_T0 = time.time()


def main():
    t = time.time()
    get_ocr()          # pay the load cost before accepting traffic
    sys.stderr.write("[npu-ocr] ready in %.1fs, listening on %s:%d\n"
                     % (time.time() - t, HOST, PORT))
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
