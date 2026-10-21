#!/usr/bin/env python3
"""Generic ONNX -> RKNN converter (shape-set aware)."""
import os
import sys
import time

from rknn.api import RKNN

SRC = os.environ["CONV_SRC"]
DST = os.environ["CONV_DST"]
MODE = os.environ.get("CONV_MODE", "dynamic")
SHAPE = os.environ.get("CONV_SHAPE", "3,48,320")
WIDTHS = [int(w) for w in os.environ.get("CONV_WIDTHS", "").split(",") if w]
DYN_SHAPE = os.environ.get("CONV_DYN_SHAPE", "")  # e.g. 1,3,960,960
DYN_SHAPES = os.environ.get("CONV_DYN_SHAPES", "")  # e.g. 1,3,736,2176|1,3,992,736
LEVEL = int(os.environ.get("RKNN_OPT_LEVEL", "1"))
QUANT = os.environ.get("RKNN_QUANT", "0") == "1"
FLOAT_DTYPE = os.environ.get("CONV_FLOAT", "float16")

print("src=%s dst=%s mode=%s shape=%s widths=%s dyn=%s dyns=%s level=%d quant=%s"
      % (SRC, DST, MODE, SHAPE, WIDTHS, DYN_SHAPE, DYN_SHAPES, LEVEL, QUANT),
      flush=True)

rknn = RKNN(verbose=True)
if DYN_SHAPES:
    groups = []
    for g in DYN_SHAPES.split("@"):
        groups.append([[int(x) for x in g.split(",")]])
    rknn.config(target_platform="rk3588", dynamic_input=groups,
                optimization_level=LEVEL)
    ret = rknn.load_onnx(SRC)
elif MODE == "plain":
    # no dynamic_input at all: keep the ONNX's own dynamic dimensions
    rknn.config(target_platform="rk3588", optimization_level=LEVEL)
    ret = rknn.load_onnx(SRC)
elif DYN_SHAPE:
    sh = [int(x) for x in DYN_SHAPE.split(",")]
    rknn.config(target_platform="rk3588", dynamic_input=[[sh]],
                optimization_level=LEVEL)
    ret = rknn.load_onnx(SRC)
elif MODE == "dynamic" and WIDTHS:
    rknn.config(target_platform="rk3588",
                dynamic_input=[[[1, 3, 48, w]] for w in WIDTHS],
                optimization_level=LEVEL)
    ret = rknn.load_onnx(SRC)
elif MODE == "dynamic":
    rknn.config(target_platform="rk3588", dynamic_input=True,
                optimization_level=LEVEL)
    ret = rknn.load_onnx(SRC)
else:
    shape = [int(x) for x in SHAPE.split(",")]
    rknn.config(target_platform="rk3588", optimization_level=LEVEL)
    ret = rknn.load_onnx(SRC, inputs=["x"], input_size_list=[shape])
print("load_onnx:", ret, flush=True)
if ret != 0:
    sys.exit(1)

t0 = time.time()
if QUANT:
    ret = rknn.build(do_quantization=True,
                     dataset=os.environ.get("RKNN_DATASET", ""))
else:
    ret = rknn.build(do_quantization=False)
print("build:", ret, "%.1fs" % (time.time() - t0), flush=True)
if ret != 0:
    sys.exit(2)
ret = rknn.export_rknn(DST)
print("export:", ret, DST,
      os.path.getsize(DST) if os.path.exists(DST) else 0, flush=True)

# remember the supported input shapes for the runtime
shapes = []
if DYN_SHAPES:
    for g in DYN_SHAPES.split("@"):
        shapes.append([int(x) for x in g.split(",")])
elif DYN_SHAPE:
    shapes.append([int(x) for x in DYN_SHAPE.split(",")])
elif WIDTHS:
    shapes = [[1, 3, 48, w] for w in WIDTHS]
if shapes:
    with open(DST + ".sizes.json", "w") as f:
        f.write(repr(shapes))
    print("wrote", DST + ".sizes.json", flush=True)
