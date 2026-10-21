#!/usr/bin/env python3
"""Client for the RK3588 NPU OCR service.

    python npu_ocr_client.py <image> [image ...]
    python npu_ocr_client.py --server http://192.168.31.230:8766 <image>
    python npu_ocr_client.py --json <image>          # raw JSON
    python npu_ocr_client.py --text <image>          # plain text only
    python npu_ocr_client.py --url <url>             # fetch the image first

Only the standard library is used, so it runs on a bare Windows Python.
"""
import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

# The Windows console defaults to a legacy code page, which mangles the
# Chinese output.  Force UTF-8 so the text shows up correctly.
if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

DEFAULT_SERVER = os.environ.get("NPU_OCR_SERVER", "http://192.168.31.230:8766")


def health(server, timeout=10):
    with urllib.request.urlopen(server + "/health", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ocr_file(server, path, timeout=120):
    """Upload one file as multipart/form-data."""
    boundary = "----npuocr" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        data = f.read()
    name = os.path.basename(path)
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="file"; filename="%s"\r\n'
         % name).encode(),
        ("Content-Type: %s\r\n\r\n" % ctype).encode(),
        data,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    req = urllib.request.Request(
        server + "/ocr", data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                 "Content-Length": str(len(body))})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ocr_bytes(server, data, name="image", timeout=120):
    req = urllib.request.Request(
        server + "/ocr", data=data, method="POST",
        headers={"Content-Type": "application/octet-stream",
                 "Content-Length": str(len(data))})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description="RK3588 NPU OCR client")
    ap.add_argument("images", nargs="*", help="local image files")
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--json", action="store_true", help="print the raw JSON")
    ap.add_argument("--text", action="store_true", help="print plain text only")
    ap.add_argument("--url", help="download the image from this URL first")
    ap.add_argument("--boxes", action="store_true", help="also print the boxes")
    args = ap.parse_args()

    try:
        h = health(args.server)
    except Exception as e:  # noqa: BLE001
        print("cannot reach %s: %s" % (args.server, e), file=sys.stderr)
        return 2
    if not args.text:
        print("[server] %s  workers=%s  det=%s  rec=%s"
              % (args.server, h.get("workers"), h.get("det"), h.get("rec")))

    if args.url:
        import urllib.request as u
        data = u.urlopen(args.url, timeout=60).read()
        res = ocr_bytes(args.server, data, "url")
        res = res[0] if isinstance(res, list) else res
        _print(res, args)
        return 0

    if not args.images:
        ap.print_help()
        return 2

    rc = 0
    for p in args.images:
        if not os.path.exists(p):
            print("no such file: %s" % p, file=sys.stderr)
            rc = 1
            continue
        t0 = time.time()
        try:
            res = ocr_file(args.server, p)
        except urllib.error.HTTPError as e:
            print("%s -> HTTP %d %s" % (p, e.code, e.read()[:200]),
                  file=sys.stderr)
            rc = 1
            continue
        dt = time.time() - t0
        res = res[0] if isinstance(res, list) else res
        if not args.text:
            print("=== %s  (%d lines, %.2fs) ===" % (p, res.get("count", 0), dt))
        _print(res, args)
    return rc


def _print(res, args):
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    lines = res.get("lines", [])
    if args.text:
        for l in lines:
            print(l["text"])
        return
    for l in lines:
        print("%s\t(%.3f)" % (l["text"], l["score"]))
    if args.boxes:
        print("--- boxes ---")
        for l in lines:
            print("%s\t%s" % (l["text"], l.get("box")))


if __name__ == "__main__":
    sys.exit(main())
