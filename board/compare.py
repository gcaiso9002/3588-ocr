#!/usr/bin/env python3
"""Diff the NPU pipeline against the RapidOCR (CPU) reference."""
import json
import os
import sys

import cv2

sys.path.insert(0, "/opt/npu-ocr")
import npu_ocr as N  # noqa: E402


def norm(s):
    return s.replace("／", "/").replace("（", "(").replace("）", ")")


def main():
    ref = json.load(open("/opt/npu-ocr/cpu_all.json"))
    ocr = N.NpuOcr()
    for path in sys.argv[1:]:
        img = cv2.imread(path)
        lines, timing = ocr(img, text_score=0.0)
        got = [(l["text"], l["score"]) for l in lines]
        want = [(r["text"], r["score"]) for r in ref.get(path, [])]
        print("\n=== %s   NPU=%d lines  CPU=%d lines  (%.2fs) ==="
              % (os.path.basename(path), len(got), len(want), timing["total"]))
        used = [False] * len(want)
        for t, s in got:
            hit = -1
            for i, (wt, _) in enumerate(want):
                if not used[i] and (wt == t or norm(wt) == norm(t)):
                    hit = i
                    break
            if hit >= 0:
                used[hit] = True
                print("   OK    %-38s npu=%.3f cpu=%.3f" % (t, s, want[hit][1]))
            else:
                print("   DIFF  %-38s npu=%.3f" % (t, s))
        for i, (wt, ws) in enumerate(want):
            if not used[i]:
                print("   MISS  %-38s cpu=%.3f" % (wt, ws))


if __name__ == "__main__":
    main()
