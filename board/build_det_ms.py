#!/usr/bin/env python3
"""Build the production multi-size det model and print the shape list."""
import os
import subprocess
import sys

TIER_A = [736, 992, 1280, 1536, 1792, 2048, 2176]
# common phone / camera resolutions (min side >= 736 -> RapidOCR keeps 1:1)
TIER_B = [(1088, 1920), (1088, 2400)]

shapes = []
for w in TIER_A:
    shapes.append((736, w))
    if w != 736:
        shapes.append((w, 736))
for h, w in TIER_B:
    shapes.append((h, w))
    if h != w:
        shapes.append((w, h))

shapes = sorted(set(shapes))
print("%d shapes" % len(shapes))
spec = "@".join("1,3,%d,%d" % (h, w) for h, w in shapes)

env = dict(os.environ)
env["CONV_SRC"] = "/opt/npu-ocr/models/ppocr_v4_det_logits.onnx"
env["CONV_DST"] = "/opt/npu-ocr/models/ppocr_v4_det_ms.rknn"
env["CONV_DYN_SHAPES"] = spec
print("building, this takes a few minutes ...", flush=True)
rc = subprocess.call(["/opt/rknnconv/bin/python", "-u", "conv.py"], env=env)
print("rc=%d" % rc)
sys.exit(rc)
