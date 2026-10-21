#!/usr/bin/env python3
"""Strip the final Softmax/Sigmoid off an ONNX model (generic).

The RKNN compiler runs the trailing softmax/sigmoid as a FLOAT16 NPU op
(exSoftmax13), whose exp() is inaccurate enough to flatten the output
distribution.  Cutting it off lets the caller do it in numpy float32.
"""
import os
import sys

import onnx

SRC = os.environ["STRIP_SRC"]
DST = os.environ["STRIP_DST"]

model = onnx.load(SRC)
graph = model.graph
out = graph.output[0]
producers = [n for n in graph.node if out.name in n.output]
if not producers:
    print("no producer for the graph output")
    sys.exit(1)
node = producers[0]
print("src=%s" % SRC)
print("output:", out.name, "<-", node.op_type, node.name, list(node.input))
if node.op_type not in ("Softmax", "Sigmoid"):
    print("nothing to strip")
    sys.exit(2)

logits = node.input[0]
graph.node.remove(node)
out.name = logits
keep = [vi for vi in graph.value_info if vi.name != out.name]
del graph.value_info[:]
graph.value_info.extend(keep)

onnx.checker.check_model(model)
onnx.save(model, DST)
print("wrote", DST, "-> new output:", onnx.load(DST).graph.output[0].name)
