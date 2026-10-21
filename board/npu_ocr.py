#!/usr/bin/env python3
"""PP-OCRv4 on the RK3588 NPU (RKNN) for the AIoT 3588 board.

Det / cls / rec models are RKNN builds of the PaddleOCR PP-OCRv4 ONNX models;
pre- and post-processing follows PaddleOCR / RapidOCR semantics.

Usage:
    npu-ocr <image> [...]        # print recognized text lines
    npu-ocr --json <image>       # print full result as JSON
    npu-ocr --warmup             # load the models and exit
"""
import itertools
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

os.environ.setdefault("RKNN_LOG_LEVEL", "0")  # quiet the RKNN runtime banner
try:
    from rknnlite.api.rknn_log import set_log_level_and_file_path

    set_log_level_and_file_path(5, os.devnull)  # keep stdout clean
except Exception:  # noqa: BLE001
    pass

MODEL_DIR = "/opt/npu-ocr/models"


def _pick(*names):
    """Prefer the model built without its trailing Softmax/Sigmoid."""
    for n in names:
        p = os.path.join(MODEL_DIR, n)
        if os.path.exists(p):
            return p
    return os.path.join(MODEL_DIR, names[-1])


# Production models live directly in models/; superseded experiments are kept
# under models/experimental/ and can be re-selected with
#   DET_MODEL=/opt/npu-ocr/models/experimental/<file>.rknn
DET_RKNN = os.environ.get("DET_MODEL") or _pick("ppocr_v4_det_ms.rknn",
                                                "experimental/ppocr_v4_det_exact.rknn",
                                                "experimental/ppocr_v4_det_l960.rknn")
CLS_RKNN = os.environ.get("CLS_MODEL") or _pick("ppocr_v4_cls_l192.rknn",
                                                "experimental/ppocr_v4_cls.rknn")
KEYS_TXT = os.path.join(MODEL_DIR, "ppocr_keys_v1.txt")


def _load_sizes(model_path):
    """The input shapes a compiled RKNN model accepts."""
    p = model_path + ".sizes.json"
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return eval(f.read())  # noqa: S307


DET_SIZES = _load_sizes(DET_RKNN)  # e.g. [[1, 3, 992, 736], ...]


def rapid_det_size(h, w):
    """The det input size PaddleOCR/RapidOCR uses (limit_side_len=736,
    limit_type=min, rounded to a multiple of 32)."""
    ratio = 736.0 / min(h, w) if min(h, w) < 736 else 1.0
    rh = int(round(int(h * ratio) / 32) * 32)
    rw = int(round(int(w * ratio) / 32) * 32)
    return rh, rw


def pick_det_size(h, w):
    """Closest supported size to RapidOCR's geometry (by aspect ratio)."""
    rh, rw = rapid_det_size(h, w)
    if not DET_SIZES:
        return rh, rw
    target = rw / float(rh)
    best = min(DET_SIZES, key=lambda s: (abs(s[3] / float(s[2]) - target),
                                         s[2] * s[3]))
    return best[2], best[3]

# The rec model is built without its final Softmax: the RKNN compiler turns
# that into an fp16 NPU op (exSoftmax13) whose exp() flattens the output
# distribution and drops every confidence to ~0.70.  We do the softmax in
# numpy instead (see Worker._decode).
#
# Two rec models are available:
#   ppocr_v4_rec_lmw.rknn  - accepts 16 input widths, so a wide text line can
#                            keep its natural aspect ratio like PaddleOCR
#   ppocr_v4_rec_l320.rknn - single 320 px input (wide lines get squeezed)
_MW_MODEL = os.path.join(MODEL_DIR, "ppocr_v4_rec_lmw.rknn")
_L320_MODEL = os.path.join(MODEL_DIR, "ppocr_v4_rec_l320.rknn")
_MW_WIDTHS = [320, 352, 384, 416, 448, 480, 512, 544, 576, 640,
              704, 768, 832, 896, 960, 1024]

if "REC_MODEL" in os.environ:
    REC_RKNN = os.environ["REC_MODEL"]
    REC_WIDTHS = [int(w) for w in
                  os.environ.get("REC_WIDTHS", "320").split(",") if w]
elif os.environ.get("REC_WIDE", "1") == "1" and os.path.exists(_MW_MODEL):
    REC_RKNN = _MW_MODEL
    REC_WIDTHS = list(_MW_WIDTHS)
else:
    REC_RKNN = _L320_MODEL
    REC_WIDTHS = [320]
# auto  : use the natural width (model must support it), else squeeze
# stretch: always squeeze the crop into the widest supported width
# chunk : split an over-wide crop into pieces (cuts characters in half)
REC_MODE = os.environ.get("REC_MODE", "auto")
MIN_SCORE = float(os.environ.get("MIN_SCORE", "0.5"))
# stretch = fill the whole det canvas, rapid = PaddleOCR's own geometry
DET_MODE = os.environ.get("DET_MODE", "rapid")
# PaddleOCR/RapidOCR only rotates a crop when the cls score is above 0.9
CLS_THRESH = float(os.environ.get("CLS_THRESH", "0.9"))
# How many NPU cores to use for cls+rec.  The RK3588 has three; every worker
# owns one core and takes crops from a shared queue.
NPU_WORKERS = int(os.environ.get("NPU_WORKERS", "3"))
# The rec model is built without the trailing Softmax, so its output is raw
# logits.  Set to 0 only if you swap in a model that still has the Softmax.
REC_LOGITS = os.environ.get("REC_LOGITS", "1") == "1"

DET_SIZE = 960          # fixed RKNN input edge (multiple of 32)
REC_H, REC_W = 48, 320  # fixed RKNN rec input
CLS_H, CLS_W = 48, 192

# ---------------------------------------------------------------- post process
# DB post-processing, ported from RapidOCR (Apache-2.0, SWHL).
import pyclipper  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402


class DBPostProcess:
    def __init__(self, thresh=0.3, box_thresh=0.5, max_candidates=1000,
                 unclip_ratio=1.6, score_mode="fast", use_dilation=True):
        self.thresh = thresh
        self.box_thresh = box_thresh
        self.max_candidates = max_candidates
        self.unclip_ratio = unclip_ratio
        self.min_size = 3
        self.score_mode = score_mode
        self.use_dilation = use_dilation
        self.dilation_kernel = None
        if use_dilation:
            k = int(os.environ.get("DET_DILATE", "2"))
            self.dilation_kernel = np.ones((k, k), np.uint8) if k > 1 else None

    def __call__(self, pred, ori_shape):
        src_h, src_w = ori_shape
        pred = pred[:, 0, :, :]
        segmentation = pred > self.thresh
        mask = segmentation[0]
        if self.dilation_kernel is not None:
            mask = cv2.dilate(np.array(mask).astype(np.uint8), self.dilation_kernel)
        return self.boxes_from_bitmap(pred[0], mask, src_w, src_h)

    def boxes_from_bitmap(self, pred, bitmap, dest_width, dest_height):
        height, width = bitmap.shape
        outs = cv2.findContours((bitmap * 255).astype(np.uint8), cv2.RETR_LIST,
                                 cv2.CHAIN_APPROX_SIMPLE)
        contours = outs[0] if len(outs) == 2 else outs[1]

        boxes, scores = [], []
        for index in range(min(len(contours), self.max_candidates)):
            contour = contours[index]
            points, sside = self.get_mini_boxes(contour)
            if sside < self.min_size:
                continue
            if self.score_mode == "fast":
                score = self.box_score_fast(pred, points.reshape(-1, 2))
            else:
                score = self.box_score_slow(pred, contour)
            if self.box_thresh > score:
                continue
            box = self.unclip(points)
            box, sside = self.get_mini_boxes(box)
            if sside < self.min_size + 2:
                continue
            box[:, 0] = np.clip(np.round(box[:, 0] / width * dest_width), 0, dest_width)
            box[:, 1] = np.clip(np.round(box[:, 1] / height * dest_height), 0, dest_height)
            boxes.append(box.astype(np.int32))
            scores.append(score)
        return np.array(boxes, dtype=np.int32), scores

    def get_mini_boxes(self, contour):
        bounding_box = cv2.minAreaRect(contour)
        points = sorted(list(cv2.boxPoints(bounding_box)), key=lambda x: x[0])
        index_1, index_2, index_3, index_4 = 0, 1, 2, 3
        if points[1][1] > points[0][1]:
            index_1, index_4 = 0, 1
        else:
            index_1, index_4 = 1, 0
        if points[3][1] > points[2][1]:
            index_2, index_3 = 2, 3
        else:
            index_2, index_3 = 3, 2
        return (np.array([points[index_1], points[index_2], points[index_3], points[index_4]]),
                min(bounding_box[1]))

    @staticmethod
    def box_score_fast(bitmap, _box):
        h, w = bitmap.shape[:2]
        box = _box.copy()
        xmin = np.clip(np.floor(box[:, 0].min()).astype(np.int32), 0, w - 1)
        xmax = np.clip(np.ceil(box[:, 0].max()).astype(np.int32), 0, w - 1)
        ymin = np.clip(np.floor(box[:, 1].min()).astype(np.int32), 0, h - 1)
        ymax = np.clip(np.ceil(box[:, 1].max()).astype(np.int32), 0, h - 1)
        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        box[:, 0] = box[:, 0] - xmin
        box[:, 1] = box[:, 1] - ymin
        cv2.fillPoly(mask, box.reshape(1, -1, 2).astype(np.int32), 1)
        return cv2.mean(bitmap[ymin:ymax + 1, xmin:xmax + 1], mask)[0]

    @staticmethod
    def box_score_slow(bitmap, contour):
        h, w = bitmap.shape[:2]
        contour = contour.copy().reshape((-1, 2))
        xmin = np.clip(np.min(contour[:, 0]), 0, w - 1)
        xmax = np.clip(np.max(contour[:, 0]), 0, w - 1)
        ymin = np.clip(np.min(contour[:, 1]), 0, h - 1)
        ymax = np.clip(np.max(contour[:, 1]), 0, h - 1)
        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        contour[:, 0] = contour[:, 0] - xmin
        contour[:, 1] = contour[:, 1] - ymin
        cv2.fillPoly(mask, contour.reshape(1, -1, 2).astype(np.int32), 1)
        return cv2.mean(bitmap[ymin:ymax + 1, xmin:xmax + 1], mask)[0]

    def unclip(self, box):
        poly = Polygon(box)
        distance = poly.area * self.unclip_ratio / poly.length
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(box, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        return np.array(offset.Execute(distance)).reshape((-1, 1, 2))


class CTCLabelDecode:
    """CTC greedy decode, ported from RapidOCR (Apache-2.0, SWHL)."""

    def __init__(self, character_path):
        with open(character_path, "rb") as f:
            chars = [ln.decode("utf-8").strip("\n").strip("\r\n")
                     for ln in f.readlines()]
        chars.insert(len(chars), " ")
        chars.insert(0, "blank")
        self.character = chars

    def __call__(self, preds):
        preds_idx = preds.argmax(axis=2)
        preds_prob = preds.max(axis=2)
        results = []
        for i in range(len(preds_idx)):
            results.append(self._one(preds_idx[i], preds_prob[i]))
        return results

    def _one(self, idx, prob):
        selection = np.ones(len(idx), dtype=bool)
        selection[1:] = idx[1:] != idx[:-1]
        selection &= idx != 0  # blank
        conf = np.array(prob[selection]).tolist()
        if len(conf) == 0:
            conf = [0]
        text = "".join(self.character[t] for t in idx[selection])
        return text, float(np.mean(conf))

    def decode_logits(self, logits):
        """Greedy CTC decode straight from the raw logits.

        The model is built without its trailing Softmax (see the note on
        exSoftmax13), so the probabilities have to be computed here.  Only
        the *selected* timesteps need a probability, and for those the
        denominator is the softmax normaliser of their own row -- so we
        exp() the selected rows only instead of the whole (T x 6625)
        tensor.  Same numbers, ~6x less work.
        """
        o = np.asarray(logits, dtype="float32")
        if o.ndim == 2:
            o = o[np.newaxis, ...]
        is_logits = REC_LOGITS or (o.size and o.max() > 1.5)
        results = []
        for i in range(o.shape[0]):
            lg = o[i]                                  # (T, C)
            idx = lg.argmax(axis=-1)                   # (T,)
            selection = np.ones(len(idx), dtype=bool)
            selection[1:] = idx[1:] != idx[:-1]
            selection &= idx != 0
            sel = np.flatnonzero(selection)
            if len(sel) == 0:
                results.append(("", 0.0))
                continue
            if is_logits:
                sub = lg[sel]                          # (K, C)
                m = sub.max(axis=-1, keepdims=True)
                e = np.exp(sub - m)
                den = e.sum(axis=-1)
                p = e[np.arange(len(sel)), idx[sel]] / den
            else:
                p = lg[sel, idx[sel]]
            text = "".join(self.character[t] for t in idx[sel])
            results.append((text, float(np.mean(p))))
        return results


# ---------------------------------------------------------------- npu wrappers
try:
    from rknnlite.api.rknn_lite import RKNNLite
except ImportError:
    from rknnlite import RKNNLite

# One core per worker.  RK3588 exposes three NPU cores; pinning a runtime to
# one of them lets several runtimes work at the same time.
_CORE_MASKS = [RKNNLite.NPU_CORE_0, RKNNLite.NPU_CORE_1, RKNNLite.NPU_CORE_2,
               RKNNLite.NPU_CORE_0_1, RKNNLite.NPU_CORE_0_1_2]
# Which cores the det graph may use.  One big graph spreads better over all
# three cores; '0' pins it to core 0, 'auto' lets the runtime decide.
_DET_MASKS = {
    '0': RKNNLite.NPU_CORE_0,
    '0_1': RKNNLite.NPU_CORE_0_1,
    '0_1_2': RKNNLite.NPU_CORE_0_1_2,
    'auto': None,
}
DET_CORE_MASK = os.environ.get('DET_CORE_MASK', '0_1_2')


def normalize(img):
    """PaddleOCR normalisation, kept in HWC (the RKNN models are NHWC).

    (x/255 - 0.5)/0.5 == x/127.5 - 1.  Done in place so a multi-megapixel
    image is not copied three times.
    """
    x = img.astype(np.float32)
    x *= (1.0 / 127.5)
    x -= 1.0
    return x


def softmax_last(o):
    """Softmax over the last axis, in float32."""
    o = np.asarray(o, dtype="float32")
    o = o - o.max(axis=-1, keepdims=True)
    np.exp(o, out=o)
    return o / o.sum(axis=-1, keepdims=True)


def sigmoid_if_logits(o):
    """Apply a sigmoid when the model returns raw logits (in place)."""
    o = np.asarray(o, dtype="float32")
    if o.size and (o.max() > 1.0 or o.min() < 0.0):
        np.clip(o, -60.0, 60.0, out=o)
        np.negative(o, out=o)
        np.exp(o, out=o)
        o += 1.0
        np.reciprocal(o, out=o)
    return o


class Det:
    def __init__(self, path):
        self.r = RKNNLite(verbose=False)
        self.r.load_rknn(path)
        # The det graph is one big layer stack; letting the runtime spread it
        # over the three NPU cores is ~20% faster than a single core.
        try:
            mask = _DET_MASKS.get(DET_CORE_MASK)
            if mask is None:
                self.r.init_runtime()
            else:
                self.r.init_runtime(core_mask=mask)
        except Exception:  # noqa: BLE001  (older runtime without core_mask)
            self.r.init_runtime()
        self.post = DBPostProcess(
            thresh=float(os.environ.get("DET_THRESH", "0.3")))

    def __call__(self, img):
        h, w = img.shape[:2]
        if DET_MODE == "stretch":
            # Fill the whole canvas: a black border around the content
            # changes the DB probability map enough to lose text regions.
            resized = cv2.resize(img, (DET_SIZE, DET_SIZE))
            canvas = np.zeros((1, DET_SIZE, DET_SIZE, 3), dtype=np.float32)
            canvas[0] = normalize(resized)
            out = self.r.inference(inputs=[canvas])
            prob = np.asarray(out[0])
        else:
            # RapidOCR's own geometry (the image fills the whole input, no
            # padding), snapped to a size the model was compiled for.
            sh, sw = pick_det_size(h, w)
            resized = cv2.resize(img, (sw, sh))
            canvas = np.zeros((1, sh, sw, 3), dtype=np.float32)
            canvas[0] = normalize(resized)
            out = self.r.inference(inputs=[canvas])
            prob = np.asarray(out[0])
        prob = sigmoid_if_logits(prob)
        boxes, scores = self.post(prob, (h, w))
        return boxes, scores


class Worker:
    """One RKNNLite runtime pair (cls + rec) pinned to a single NPU core."""

    def __init__(self, rec_path, cls_path, keys_path, widths, core):
        self.core = core
        mask = _CORE_MASKS[core % len(_CORE_MASKS)]
        self.cls = None
        if cls_path and os.path.exists(cls_path):
            self.cls = RKNNLite(verbose=False)
            self.cls.load_rknn(cls_path)
            self.cls.init_runtime(core_mask=mask)
        self.rec = RKNNLite(verbose=False)
        self.rec.load_rknn(rec_path)
        self.rec.init_runtime(core_mask=mask)
        self.decode = CTCLabelDecode(keys_path)
        self.widths = sorted(widths)
        self._buf = {}      # rec input buffers, one per width
        self._cls_buf = None

    # ---- cls -----------------------------------------------------------
    def cls_crop(self, crop):
        if self.cls is None:
            return crop
        # PaddleOCR semantics: keep the aspect ratio and pad on the right.
        # Stretching the crop to the full 192 px changes the cls score and
        # makes the model rotate text that should stay upright.
        h, w = crop.shape[:2]
        ratio = w / float(h)
        if math.ceil(CLS_H * ratio) > CLS_W:
            rw = CLS_W
        else:
            rw = int(math.ceil(CLS_H * ratio))
        resized = cv2.resize(crop, (rw, CLS_H)).astype("float32")
        resized *= (1.0 / 127.5)
        resized -= 1.0
        canvas = self._cls_buf
        if canvas is None:
            canvas = np.zeros((1, CLS_H, CLS_W, 3), dtype=np.float32)
            self._cls_buf = canvas
        elif rw < CLS_W:
            canvas[0, :, rw:, :] = 0.0
        canvas[0, :, :rw, :] = resized
        out = np.asarray(self.cls.inference(inputs=[canvas])[0]).reshape(-1)
        if out.max() > 1.5:          # raw logits -> softmax
            out = softmax_last(out)
        label = int(np.argmax(out))
        if label == 1 and float(out[1]) > CLS_THRESH:
            return cv2.rotate(crop, cv2.ROTATE_180)
        return crop

    # ---- rec -----------------------------------------------------------
    def _resize_pad(self, crop, width=REC_W):
        """PaddleOCR semantics: keep the aspect ratio, pad on the right.

        PaddleOCR feeds the rec net a variable-width image
        (img_width = 48 * max_wh_ratio); here the width has to be one of
        the widths the RKNN model was compiled for.  If the crop is wider
        than every supported width it gets squeezed horizontally, which
        degrades but is still far better than cutting characters apart.
        """
        h, w = crop.shape[:2]
        ratio = w / float(h)
        rw = int(math.ceil(REC_H * ratio))
        if rw > width:
            resized = cv2.resize(crop, (width, REC_H)).astype("float32")
            rw = width
        else:
            resized = cv2.resize(crop, (rw, REC_H)).astype("float32")
        resized *= (1.0 / 127.5)
        resized -= 1.0
        # Reuse one buffer per width: the workers run in parallel, so
        # allocating a fresh multi-hundred-KB array per call is pure GIL
        # contention.
        pad = self._buf.get(width)
        if pad is None:
            pad = np.zeros((1, REC_H, width, 3), dtype=np.float32)
            self._buf[width] = pad
        elif rw < width:
            pad[0, :, rw:, :] = 0.0     # the padding area has to be cleared
        pad[0, :, :rw, :] = resized
        return pad

    @staticmethod
    def _chunk(crop, ratio_thresh=REC_W / float(REC_H)):
        h, w = crop.shape[:2]
        ratio = w / float(h)
        if ratio <= ratio_thresh:
            return [crop]
        n = int(math.ceil(ratio / ratio_thresh))
        chunk_w = int(math.ceil(w / float(n)))
        out = []
        for i in range(n):
            part = crop[:, i * chunk_w:(i + 1) * chunk_w]
            if part.shape[1] < 8:
                break
            out.append(part)
        return out

    def _width_for(self, crop):
        h, w = crop.shape[:2]
        need = int(math.ceil(REC_H * (w / float(h))))
        for cand in self.widths:
            if cand >= need:
                return cand
        return self.widths[-1]

    def _rec_one(self, crop):
        """Recognise a single crop; returns (text, score)."""
        h, w = crop.shape[:2]
        ratio = w / float(h)
        if REC_MODE == "chunk" and ratio > REC_W / float(REC_H):
            text, score, n = "", 0.0, 0
            for part in self._chunk(crop):
                x = self._resize_pad(part)
                out = self.rec.inference(inputs=[x])
                preds = np.asarray(out[0], dtype="float32")
                if preds.ndim == 3 and preds.shape[1] != preds.shape[2]:
                    if preds.shape[2] < preds.shape[1]:
                        preds = preds.transpose(0, 2, 1)
                (t, s), = self.decode.decode_logits(preds)
                text += t
                score += s
                n += 1
            return text, (score / n if n else 0.0)

        width = self._width_for(crop)
        x = self._resize_pad(crop, width)
        out = self.rec.inference(inputs=[x])
        preds = np.asarray(out[0], dtype="float32")
        if preds.ndim == 3 and preds.shape[1] != preds.shape[2]:
            # (N, C, T) instead of (N, T, C)
            if preds.shape[2] < preds.shape[1]:
                preds = preds.transpose(0, 2, 1)
        (text, score), = self.decode.decode_logits(preds)
        return text, float(score)

    def run(self, crop):
        return self._rec_one(self.cls_crop(crop))


class Recognizer:
    """cls + rec over a list of crops, spread over the NPU cores.

    Every worker owns one RKNNLite runtime pair on its own NPU core and
    pulls the next crop from a shared queue, so a wide line and a narrow
    line do not end up on the same core.
    """

    def __init__(self, rec_path, cls_path, keys_path, widths, workers=None):
        n = workers if workers is not None else NPU_WORKERS
        n = max(1, min(int(n), len(_CORE_MASKS)))
        self.workers = []
        self.pool = None
        if n == 1:
            self.workers = [Worker(rec_path, cls_path, keys_path, widths, 0)]
            return
        self.pool = ThreadPoolExecutor(max_workers=n)
        # Load the runtimes in parallel: each one opens the same 27 MB model
        # file and initialises its own NPU context.
        futs = [self.pool.submit(Worker, rec_path, cls_path, keys_path,
                                 widths, k) for k in range(n)]
        self.workers = [f.result() for f in futs]

    def __call__(self, crops):
        if not crops:
            return []
        if self.pool is None:
            w = self.workers[0]
            return [w.run(c) for c in crops]
        out = [None] * len(crops)
        lock = threading.Lock()
        state = {"i": 0}

        def loop(k):
            w = self.workers[k]
            while True:
                with lock:
                    i = state["i"]
                    if i >= len(crops):
                        return
                    state["i"] = i + 1
                out[i] = w.run(crops[i])

        futs = [self.pool.submit(loop, k) for k in range(len(self.workers))]
        for f in futs:
            f.result()
        return out


# ------------------------------------------------------------------- pipeline
def get_rotate_crop_image(img, points):
    cw = int(max(np.linalg.norm(points[0] - points[1]),
                  np.linalg.norm(points[2] - points[3])))
    ch = int(max(np.linalg.norm(points[0] - points[3]),
                  np.linalg.norm(points[1] - points[2])))
    if cw < 1 or ch < 1:
        return None
    pts_std = np.array([[0, 0], [cw, 0], [cw, ch], [0, ch]]).astype("float32")
    M = cv2.getPerspectiveTransform(points, pts_std)
    dst = cv2.warpPerspective(img, M, (cw, ch), borderMode=cv2.BORDER_REPLICATE,
                              flags=cv2.INTER_CUBIC)
    if dst.shape[0] * 1.0 / dst.shape[1] >= 1.5:
        dst = np.rot90(dst)
    return dst


def order_points_clockwise(pts):
    xSorted = pts[np.argsort(pts[:, 0]), :]
    leftMost, rightMost = xSorted[:2, :], xSorted[2:, :]
    leftMost = leftMost[np.argsort(leftMost[:, 1]), :]
    tl, bl = leftMost
    rightMost = rightMost[np.argsort(rightMost[:, 1]), :]
    tr, br = rightMost
    return np.array([tl, tr, br, bl], dtype="float32")


def filter_boxes(boxes, shape):
    h, w = shape
    out = []
    for box in boxes:
        box = order_points_clockwise(box)
        for p in range(box.shape[0]):
            box[p, 0] = int(min(max(box[p, 0], 0), w - 1))
            box[p, 1] = int(min(max(box[p, 1], 0), h - 1))
        rw = int(np.linalg.norm(box[0] - box[1]))
        rh = int(np.linalg.norm(box[0] - box[3]))
        if rw <= 3 or rh <= 3:
            continue
        out.append(box)
    return out


class NpuOcr:
    def __init__(self):
        t = time.time()
        self.det = Det(DET_RKNN)
        self.recognizer = Recognizer(REC_RKNN, CLS_RKNN, KEYS_TXT, REC_WIDTHS)
        print("[npu-ocr] models ready in %.1fs" % (time.time() - t), file=sys.stderr)

    def __call__(self, img, text_score=None):
        t0 = time.time()
        if text_score is None:
            text_score = MIN_SCORE
        boxes, scores = self.det(img)
        t_det = time.time() - t0
        boxes = filter_boxes(boxes, img.shape[:2])
        if not len(boxes):
            return [], {"det": t_det, "rec": 0.0, "total": time.time() - t0}

        crops = []
        for box in boxes:
            c = get_rotate_crop_image(img, box.copy())
            if c is not None and c.size > 0:
                crops.append(c)
        t1 = time.time()
        recs = self.recognizer(crops)
        t_rec = time.time() - t1

        lines = []
        for (text, score), box in zip(recs, boxes):
            if score < text_score or not text.strip():
                continue
            lines.append({"text": text, "score": round(float(score), 4),
                          "box": [[int(p[0]), int(p[1])] for p in box]})
        return lines, {"det": round(t_det, 3), "rec": round(t_rec, 3),
                       "total": round(time.time() - t0, 3)}


def main():
    argv = sys.argv[1:]
    as_json = "--json" in argv
    if "--warmup" in argv:
        NpuOcr()
        return 0
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    ocr = NpuOcr()
    for p in paths:
        img = cv2.imread(p)
        if img is None:
            print("[npu-ocr] cannot read %s" % p, file=sys.stderr)
            continue
        lines, timing = ocr(img)
        if as_json:
            print(json.dumps({"image": p, "timing": timing, "lines": lines},
                             ensure_ascii=False, indent=2))
        else:
            print("=== %s  (%d lines, det %.2fs rec %.2fs total %.2fs) ==="
                  % (p, len(lines), timing["det"], timing["rec"], timing["total"]))
            for ln in lines:
                print("%s\t(%.3f)" % (ln["text"], ln["score"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
