#!/usr/bin/env python3
"""npu-ocr 快捷截图取字 —— Windows 桌面版（仿天若OCR / 白描电脑版）

用法：
    python npu_ocr_gui.py              # 常驻，托盘图标，全局快捷键截图取字
    python npu_ocr_gui.py --selftest   # 自检：抓屏 -> 裁一块 -> 调板子 API
    python npu_ocr_gui.py --shot       # 不等快捷键，直接进入截图模式

识别在 RK3588 板子上跑（NPU），本程序只负责截图、选区、取词、复制/粘贴。

依赖：Pillow、tkinter、pywin32（Windows 自带的 pywin32 即可），可选 pystray。
配置：同目录 npu_ocr_gui_config.json，首次运行自动生成。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

# ---------------------------------------------------------------- DPI aware
# 必须在创建任何窗口之前设置，否则 Tk 用的是逻辑坐标而截图用的是物理像素，
# 多屏 + 缩放下框会整体错位。
def _set_dpi_aware():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4))          # PER_MONITOR_AWARE_V2
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


_set_dpi_aware()

import tkinter as tk                                    # noqa: E402
from tkinter import ttk                                 # noqa: E402

from PIL import Image, ImageGrab, ImageTk              # noqa: E402

try:
    import win32api, win32con, win32gui, win32clipboard  # noqa: E402
    import win32con as _w32c
    HAVE_WIN32 = True
except Exception:                                        # noqa: BLE001
    HAVE_WIN32 = False

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "npu_ocr_gui_config.json")

# Generic alternatives used when the configured hotkey is already taken by
# another program (Windows error 1409).  They are derived from the modifier
# set of the configured hotkey, not from what happens to be free on one
# particular machine.
HOTKEY_KEYS = ["2", "3", "4", "5", "6", "7", "8", "9", "0",
               "q", "w", "e", "r", "s", "d", "f", "g", "z", "x", "c", "v", "b",
               "f2", "f3", "f4", "f5", "f6", "f7", "f8"]

DEFAULT_CFG = {
    "server": "http://192.168.31.230:8766",
    "server_candidates": ["http://192.168.31.40:8766"],
    "hotkey": "ctrl+1",
    "auto_copy": True,
    "auto_paste": False,
    "magnifier": True,
    "keep_result_window": False,
    "log_file": "",
}

# ------------------------------------------------------------------- logging
_LOG = None


def log(msg):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, file=sys.stderr)
    if _LOG:
        try:
            with open(_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:  # noqa: BLE001
        pass
    return cfg


def save_cfg(cfg):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        log("cannot save config: %r" % e)


# ------------------------------------------------------------------- hotkey
MODS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}
VK = {"f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
      "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
      "space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09,
      "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
      ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF}
for _c in "0123456789":
    VK[_c] = ord(_c)
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK[_c] = ord(_c.upper())


def parse_hotkey(spec):
    """'ctrl+alt+a' -> (modifier mask, virtual key)."""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        return 0, 0
    mods = 0
    key = 0
    for p in parts:
        if p in MODS:
            mods |= MODS[p]
        elif p in VK:
            key = VK[p]
    return mods, key


def mods_to_spec(mods, key):
    parts = []
    if mods & MODS["ctrl"]:
        parts.append("ctrl")
    if mods & MODS["alt"]:
        parts.append("alt")
    if mods & MODS["shift"]:
        parts.append("shift")
    if mods & MODS["win"]:
        parts.append("win")
    parts.append(key)
    return "+".join(parts)


def hotkey_alternatives(spec):
    """Generic alternatives to a hotkey that is already taken.

    Keeps the modifier set of the configured hotkey and swaps the key, then
    tries a couple of other modifier sets.  Works on any machine.
    """
    mods, _ = parse_hotkey(spec)
    out = []
    for k in HOTKEY_KEYS:
        out.append(mods_to_spec(mods, k))
    for k in ("a", "s", "d", "q", "w", "e"):
        out.append("ctrl+alt+" + k)
    for k in ("a", "s", "d", "q"):
        out.append("ctrl+shift+" + k)
    seen = {str(spec).lower()}
    return [s for s in out if s.lower() not in seen and not seen.add(s.lower())]


class Hotkey:
    """A global hotkey registered with RegisterHotKey (no extra deps)."""

    WM_HOTKEY = 0x0312
    MOD_NOREPEAT = 0x4000

    def __init__(self, spec, callback):
        self.spec = spec
        self.callback = callback
        self.mods, self.vk = parse_hotkey(spec)
        self.ok = False
        self._thread = None
        self._stop = False

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        time.sleep(0.2)
        return self.ok

    def _loop(self):
        user32 = ctypes.windll.user32
        if self.mods & 0x4000:      # the MOD_NOREPEAT bit is 0x4000
            self.mods &= ~0x4000
        reg = user32.RegisterHotKey(None, 0xBEEF, self.mods | self.MOD_NOREPEAT,
                                    self.vk)
        if not reg:
            log("RegisterHotKey failed for %r (already taken?)" % self.spec)
            return
        self.ok = True
        log("hotkey registered: %s" % self.spec)
        msg = wt.MSG()
        while not self._stop:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            if msg.message == self.WM_HOTKEY:
                try:
                    self.callback()
                except Exception as e:  # noqa: BLE001
                    log("hotkey callback failed: %r" % e)
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self):
        self._stop = True
        try:
            ctypes.windll.user32.UnregisterHotKey(None, 0xBEEF)
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------- screen
def virtual_screen():
    """(x, y, w, h) of the whole virtual desktop, in physical pixels."""
    u = ctypes.windll.user32
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    x = u.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = u.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = u.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = u.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, w, h


def grab_screen():
    """Grab the whole virtual desktop.  Pixel (px, py) of the result is the
    screen point (vx+px, vy+py)."""
    return ImageGrab.grab(all_screens=True)


# ----------------------------------------------------------------- clipboard
def copy_text(text):
    if not text:
        return False
    if not HAVE_WIN32:
        return False
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    return True


def get_clipboard_text():
    if not HAVE_WIN32:
        return ""
    try:
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:  # noqa: BLE001
        return ""


def foreground_window():
    if not HAVE_WIN32:
        return 0
    try:
        return win32gui.GetForegroundWindow()
    except Exception:  # noqa: BLE001
        return 0


def restore_foreground(hwnd):
    if not HAVE_WIN32 or not hwnd:
        return
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass


def send_paste():
    """Ctrl+V into whatever has the focus."""
    if not HAVE_WIN32:
        return False
    try:
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(win32con.VK_V, 0, 0, 0)
        win32api.keybd_event(win32con.VK_V, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        return True
    except Exception as e:  # noqa: BLE001
        log("paste failed: %r" % e)
        return False


# --------------------------------------------------------------------- OCR
def server_health(server, timeout=4):
    try:
        with urllib.request.urlopen(server.rstrip("/") + "/health",
                                    timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if data.get("ok") else None
    except Exception:  # noqa: BLE001
        return None


def discover_server(configured, extra=None):
    """Find a reachable OCR server.

    The configured address wins; if it is down (board rebooted, IP changed)
    a short list of well-known addresses is probed and the working one is
    returned so the app keeps working without touching the settings.
    """
    cands = [configured]
    for c in list(extra or []) + list(DEFAULT_CFG.get("server_candidates", [])):
        if c and c not in cands:
            cands.append(c)
    for c in cands:
        if server_health(c):
            return c
    return None


def ocr_image(server, image, timeout=60):
    """Send the image to the board, return (text, lines, elapsed)."""
    import io
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()
    boundary = "----npuocr" + uuid.uuid4().hex
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        b'Content-Disposition: form-data; name="file"; filename="shot.png"\r\n',
        b"Content-Type: image/png\r\n\r\n",
        data,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    req = urllib.request.Request(
        server.rstrip("/") + "/ocr", data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                 "Content-Length": str(len(body))})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8"))
    dt = time.time() - t0
    if isinstance(payload, list):
        payload = payload[0]
    lines = payload.get("lines", [])
    text = "\n".join(l["text"] for l in lines)
    return text, lines, dt


# ----------------------------------------------------------------- overlay
class Overlay:
    """Full-screen selection overlay drawn on top of the frozen screenshot."""

    def __init__(self, master, shot, on_done, on_cancel, magnifier=True):
        self.shot = shot
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.cfg_magnifier = magnifier
        self.vx, self.vy, self.vw, self.vh = virtual_screen()
        self.start = None
        self.rect = None
        self.mag_img = None
        self.last_mag = 0.0

        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.geometry("%dx%d+%d+%d" % (self.vw, self.vh, self.vx, self.vy))
        self.top.attributes("-topmost", True)
        self.top.configure(bg="#101010")
        try:
            self.top.attributes("-alpha", 1.0)
        except Exception:  # noqa: BLE001
            pass

        self.canvas = tk.Canvas(self.top, width=self.vw, height=self.vh,
                                highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.photo = ImageTk.PhotoImage(self.shot)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

        # four dimmed bands around the selection
        self.bands = [self.canvas.create_rectangle(0, 0, 0, 0, fill="black",
                                                   outline="", stipple="gray50")
                      for _ in range(4)]
        self.border = self.canvas.create_rectangle(0, 0, 0, 0, outline="#00e5ff",
                                                   width=2)
        self.hline = self.canvas.create_line(0, 0, 0, 0, fill="#00e5ff",
                                             dash=(4, 4))
        self.vline = self.canvas.create_line(0, 0, 0, 0, fill="#00e5ff",
                                             dash=(4, 4))
        self.hint = self.canvas.create_text(
            24, 24, anchor="nw", fill="#ffffff",
            font=("Microsoft YaHei UI", 11),
            text="拖动鼠标框选要取字的区域   Esc 取消")

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.top.bind("<Escape>", self.on_esc)
        self.top.focus_force()
        self.top.attributes("-topmost", True)

    # -- helpers ---------------------------------------------------------
    def _norm(self, x0, y0, x1, y1):
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def _redraw(self):
        x0, y0, x1, y1 = self.rect
        self.canvas.coords(self.border, x0, y0, x1, y1)
        self.canvas.coords(self.hline, x0, (y0 + y1) // 2, x1, (y0 + y1) // 2)
        self.canvas.coords(self.vline, (x0 + x1) // 2, y0, (x0 + x1) // 2, y1)
        bands = [(0, 0, self.vw, y0), (0, y1, self.vw, self.vh),
                 (0, y0, x0, y1), (x1, y0, self.vw, y1)]
        for item, (a, b, c, d) in zip(self.bands, bands):
            self.canvas.coords(item, a, b, c, d)
        self.canvas.itemconfigure(self.border, state="normal")
        for item in self.bands:
            self.canvas.itemconfigure(item, state="normal")

    def _magnifier(self, cx, cy):
        if not self.cfg_magnifier:
            return
        now = time.time()
        if now - self.last_mag < 0.06:
            return
        self.last_mag = now
        mw, mh = 200, 150
        sw, sh = 100, 75
        sx = max(0, min(self.shot.width - sw, cx - sw // 2))
        sy = max(0, min(self.shot.height - sh, cy - sh // 2))
        zoom = self.shot.crop((sx, sy, sx + sw, sy + sh)).resize(
            (mw, mh), Image.NEAREST)
        if self.mag_img is None:
            self.mag_img = ImageTk.PhotoImage(zoom)
            self.mag_item = self.canvas.create_image(
                self.vw - mw - 20, self.vh - mh - 20, anchor="nw",
                image=self.mag_img)
            self.mag_frame = self.canvas.create_rectangle(
                self.vw - mw - 20, self.vh - mh - 20, self.vw - 20, self.vh - 20,
                outline="#00e5ff", width=2)
        else:
            self.mag_img.paste(zoom)
        self.canvas.coords(self.mag_item, self.vw - mw - 20, self.vh - mh - 20)
        self.canvas.coords(self.mag_frame, self.vw - mw - 20, self.vh - mh - 20,
                           self.vw - 20, self.vh - 20)

    # -- events ----------------------------------------------------------
    def on_press(self, ev):
        self.start = (ev.x, ev.y)
        self.rect = self._norm(ev.x, ev.y, ev.x, ev.y)
        self._redraw()

    def on_motion(self, ev):
        self._magnifier(ev.x, ev.y)
        if self.start is None:
            return
        self.rect = self._norm(self.start[0], self.start[1], ev.x, ev.y)
        self._redraw()

    def on_release(self, ev):
        if self.start is None:
            return
        self.rect = self._norm(self.start[0], self.start[1], ev.x, ev.y)
        x0, y0, x1, y1 = self.rect
        shot = self.shot
        self.close()
        if x1 - x0 < 6 or y1 - y0 < 6:
            log("selection too small, ignored")
            return
        # crop from the screenshot that was taken *before* the overlay
        # appeared, so the selection is exactly what the user saw
        self.on_done((x0, y0, x1, y1), shot)

    def on_esc(self, _ev):
        self.close()
        self.on_cancel()

    def close(self):
        try:
            self.top.destroy()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------ result window
class ResultWindow:
    def __init__(self, master, cfg, text, lines, elapsed, rect, prev_hwnd,
                 on_rescan, on_settings):
        self.cfg = cfg
        self.text = text
        self.prev_hwnd = prev_hwnd
        self.on_rescan = on_rescan
        self.on_settings = on_settings

        self.win = tk.Toplevel(master)
        self.win.title("npu-ocr 取字")
        self.win.resizable(True, True)
        self.win.geometry("480x320+100+100")
        self.win.attributes("-topmost", True)

        head = ttk.Frame(self.win, padding=(10, 8))
        head.pack(fill="x")
        ttk.Label(head, text="识别完成", font=("Microsoft YaHei UI", 10, "bold")
                  ).pack(side="left")
        ttk.Label(head, text="%d 行 / %.2fs" % (len(lines), elapsed)
                  ).pack(side="right")

        body = ttk.Frame(self.win, padding=(10, 0))
        body.pack(fill="both", expand=True)
        self.textbox = tk.Text(body, wrap="word", font=("Microsoft YaHei UI", 11),
                               undo=False, background="#ffffff",
                               highlightthickness=1, borderwidth=1)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.textbox.yview)
        self.textbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.textbox.pack(side="left", fill="both", expand=True)
        self.textbox.insert("1.0", text or "(没有识别到文字)")
        self.textbox.configure(state="disabled")

        bar = ttk.Frame(self.win, padding=(10, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="复制", width=8,
                   command=self.on_copy).pack(side="left")
        ttk.Button(bar, text="粘贴", width=8,
                   command=self.on_paste).pack(side="left", padx=4)
        ttk.Button(bar, text="重新识别", width=10,
                   command=self.on_rescan).pack(side="left")
        ttk.Button(bar, text="设置", width=8,
                   command=self.on_settings).pack(side="left", padx=4)
        ttk.Button(bar, text="关闭", width=8,
                   command=self.close).pack(side="right")

        self.status = ttk.Label(self.win, text="", padding=(10, 0, 10, 8))
        self.status.pack(fill="x")

        self.place_near(rect)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    def place_near(self, rect):
        try:
            vx, vy, vw, vh = virtual_screen()
            x = vx + rect[0] + 20
            y = vy + rect[1] + 20
            if x + 500 > vx + vw:
                x = vx + rect[0] - 500
            if y + 360 > vy + vh:
                y = vy + rect[3] - 340
            x = max(vx, min(x, vx + vw - 100))
            y = max(vy, min(y, vy + vh - 60))
            self.win.geometry("480x320+%d+%d" % (x, y))
        except Exception:  # noqa: BLE001
            pass

    def on_copy(self):
        if copy_text(self.text):
            self.status.config(text="已复制到剪贴板")
        else:
            self.status.config(text="复制失败")

    def on_paste(self):
        if not copy_text(self.text):
            self.status.config(text="复制失败")
            return
        restore_foreground(self.prev_hwnd)
        time.sleep(0.15)
        send_paste()
        self.status.config(text="已粘贴")
        if not self.cfg.get("keep_result_window"):
            self.close()

    def close(self):
        try:
            self.win.destroy()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------- app
class App:
    def __init__(self):
        self.cfg = load_cfg()
        global _LOG
        _LOG = self.cfg.get("log_file") or None
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("npu-ocr")
        self.hotkey = None
        self.active_hotkey = self.cfg.get("hotkey", DEFAULT_CFG["hotkey"])
        self.tray = None
        self.result_win = None
        self.overlay = None
        self.pending_rect = None
        self.prev_hwnd = 0

    # -- capture ---------------------------------------------------------
    def start_capture(self):
        if self.overlay is not None:
            return
        try:
            shot = grab_screen()
        except Exception as e:  # noqa: BLE001
            log("grab failed: %r" % e)
            return
        self.prev_hwnd = foreground_window()
        log("capture start")
        self.overlay = Overlay(self.root, shot, self.on_region, self.on_cancel,
                               magnifier=self.cfg.get("magnifier", True))

    def on_cancel(self):
        self.overlay = None
        restore_foreground(self.prev_hwnd)

    def on_region(self, rect, shot):
        self.overlay = None
        crop = shot.crop(rect)
        self.pending_rect = rect
        log("region selected: %s -> crop %s" % (rect, crop.size))
        self.run_ocr(crop)

    def run_ocr(self, crop):
        server = self.cfg.get("server", DEFAULT_CFG["server"])
        save_dir = self.cfg.get("save_dir", "")
        if save_dir:
            try:
                os.makedirs(save_dir, exist_ok=True)
                stamp = time.strftime("%H%M%S")
                crop.save(os.path.join(save_dir, "crop_%s.png" % stamp))
            except Exception as e:  # noqa: BLE001
                log("cannot save crop: %r" % e)
        try:
            text, lines, dt = ocr_image(server, crop)
        except Exception as e:  # noqa: BLE001
            log("ocr failed on %s: %r" % (server, e))
            found = discover_server(server, self.cfg.get("server_candidates"))
            if found and found != server:
                log("discovered a working server: %s" % found)
                self.cfg["server"] = found
                save_cfg(self.cfg)
                try:
                    text, lines, dt = ocr_image(found, crop)
                except Exception as e2:  # noqa: BLE001
                    log("ocr failed again: %r" % e2)
                    self.show_error("识别失败：%r" % e2)
                    restore_foreground(self.prev_hwnd)
                    return
            else:
                self.show_error("连不上板子服务：%s\n%s\n\n"
                                "请确认板子上的 npu-ocr.service 还在跑。"
                                % (server, e))
                restore_foreground(self.prev_hwnd)
                return
        log("ocr done: %d lines, %.2fs" % (len(lines), dt))
        if self.cfg.get("auto_copy") and text:
            copy_text(text)
        if self.cfg.get("auto_paste") and text:
            copy_text(text)
            restore_foreground(self.prev_hwnd)
            time.sleep(0.15)
            send_paste()
            return
        self.show_result(text, lines, dt)

    def show_error(self, msg):
        w = tk.Toplevel(self.root)
        w.title("npu-ocr")
        ttk.Label(w, text=msg, padding=16, wraplength=420).pack()
        ttk.Button(w, text="好", command=w.destroy).pack(pady=(0, 12))

    def show_result(self, text, lines, dt):
        if self.result_win is not None:
            self.result_win.close()
        self.result_win = ResultWindow(
            self.root, self.cfg, text, lines, dt, self.pending_rect or (0, 0, 0, 0),
            self.prev_hwnd, self.rescan, self.open_settings)
        if not self.cfg.get("keep_result_window"):
            self.result_win.win.after(60000, self.result_win.close)

    def rescan(self):
        if self.result_win is not None:
            self.result_win.close()
            self.result_win = None
        if self.pending_rect:
            self.start_capture()

    # -- settings --------------------------------------------------------
    def open_settings(self):
        Settings(self.root, self.cfg, self.apply_settings)

    def apply_settings(self, cfg):
        old_hot = self.cfg.get("hotkey")
        self.cfg = cfg
        save_cfg(cfg)
        global _LOG
        _LOG = cfg.get("log_file") or None
        if cfg.get("hotkey") != old_hot:
            self.set_hotkey(cfg.get("hotkey"))

    def set_hotkey(self, spec, quiet=False):
        if self.hotkey is not None:
            self.hotkey.stop()
            self.hotkey = None
        # The hotkey arrives on the RegisterHotKey thread; Tk is not thread
        # safe, so every UI call has to be marshalled onto the main loop.
        hk = Hotkey(spec, lambda: self.root.after(0, self.start_capture))
        if hk.start():
            self.hotkey = hk
            self.active_hotkey = spec
            return spec
        # taken by another program -> fall back to something that works
        for alt in hotkey_alternatives(spec):
            alt_hk = Hotkey(alt,
                            lambda: self.root.after(0, self.start_capture))
            if alt_hk.start():
                self.hotkey = alt_hk
                self.active_hotkey = alt
                log("hotkey %r is taken, using %r instead" % (spec, alt))
                if not quiet:
                    self.notify("快捷键已被占用",
                                "%s 已被其它程序占用，已自动改用 %s。\n"
                                "可在「设置」里改成别的组合。"
                                % (spec.upper(), alt.upper()))
                return alt
        log("no usable hotkey found")
        if not quiet:
            self.notify("快捷键注册失败",
                        "%s 和所有备用快捷键都注册失败，\n"
                        "请在「设置」里换一个组合。" % spec.upper())
        return None

    def notify(self, title, msg):
        try:
            w = tk.Toplevel(self.root)
            w.title(title)
            w.attributes("-topmost", True)
            ttk.Label(w, text=msg, padding=16, wraplength=380).pack()
            ttk.Button(w, text="知道了", command=w.destroy).pack(pady=(0, 10))
            w.after(9000, w.destroy)
        except Exception as e:  # noqa: BLE001
            log("notify failed: %r" % e)

    # -- tray ------------------------------------------------------------
    def make_icon(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(30, 120, 220, 255))
        d.rectangle([14, 14, 38, 46], fill=(255, 255, 255, 255))
        for y in (22, 30, 38):
            d.line([18, y, 34, y], fill=(30, 120, 220, 255), width=2)
        d.ellipse([32, 30, 52, 50], outline=(255, 255, 255, 255), width=4)
        d.line([48, 46, 58, 56], fill=(255, 255, 255, 255), width=5)
        return img

    def setup_tray(self):
        try:
            import pystray
        except Exception:  # noqa: BLE001
            log("pystray not installed, no tray icon")
            return

        def on_capture(icon, item):
            self.root.after(0, self.start_capture)

        def on_settings(icon, item):
            self.root.after(0, self.open_settings)

        def on_quit(icon, item):
            self.root.after(0, self.quit)

        menu = pystray.Menu(
            pystray.MenuItem("截图取字 (%s)" % self.active_hotkey.upper(),
                             on_capture, default=True),
            pystray.MenuItem("设置...", on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", on_quit),
        )
        self.tray = pystray.Icon("npu-ocr", self.make_icon(), "npu-ocr 截图取字",
                                 menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def quit(self):
        if self.hotkey is not None:
            self.hotkey.stop()
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.root.quit()
        except Exception:  # noqa: BLE001
            pass

    def run(self):
        self.set_hotkey(self.cfg.get("hotkey", DEFAULT_CFG["hotkey"]))
        self.setup_tray()
        log("npu-ocr gui ready, hotkey=%s server=%s"
            % (self.cfg.get("hotkey"), self.cfg.get("server")))
        self.root.mainloop()


class Settings:
    def __init__(self, master, cfg, on_apply):
        self.cfg = dict(cfg)
        self.on_apply = on_apply
        self.win = tk.Toplevel(master)
        self.win.title("npu-ocr 设置")
        self.win.resizable(False, False)
        f = ttk.Frame(self.win, padding=14)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="板子服务地址").grid(row=0, column=0, sticky="w")
        self.e_server = ttk.Entry(f, width=34)
        self.e_server.insert(0, self.cfg.get("server", ""))
        self.e_server.grid(row=0, column=1, pady=4)

        ttk.Label(f, text="全局快捷键").grid(row=1, column=0, sticky="w")
        self.e_hot = ttk.Entry(f, width=34)
        self.e_hot.insert(0, self.cfg.get("hotkey", ""))
        self.e_hot.grid(row=1, column=1, pady=4)
        ttk.Label(f, text="例：ctrl+alt+a / ctrl+shift+s / f4",
                  foreground="#666").grid(row=2, column=1, sticky="w")

        self.v_copy = tk.BooleanVar(value=self.cfg.get("auto_copy", True))
        self.v_paste = tk.BooleanVar(value=self.cfg.get("auto_paste", False))
        self.v_mag = tk.BooleanVar(value=self.cfg.get("magnifier", True))
        self.v_keep = tk.BooleanVar(value=self.cfg.get("keep_result_window", False))
        for i, (var, text) in enumerate((
                (self.v_copy, "识别后自动复制到剪贴板"),
                (self.v_paste, "识别后自动粘贴到原窗口"),
                (self.v_mag, "截图时显示放大镜"),
                (self.v_keep, "结果窗口不自动关闭"))):
            ttk.Checkbutton(f, text=text, variable=var).grid(
                row=3 + i, column=0, columnspan=2, sticky="w", pady=2)

        bar = ttk.Frame(f)
        bar.grid(row=7, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(bar, text="保存", command=self.save).pack(side="right")
        ttk.Button(bar, text="取消", command=self.win.destroy).pack(side="right",
                                                                     padx=6)

    def save(self):
        self.cfg["server"] = self.e_server.get().strip() or DEFAULT_CFG["server"]
        self.cfg["hotkey"] = self.e_hot.get().strip() or DEFAULT_CFG["hotkey"]
        self.cfg["auto_copy"] = bool(self.v_copy.get())
        self.cfg["auto_paste"] = bool(self.v_paste.get())
        self.cfg["magnifier"] = bool(self.v_mag.get())
        self.cfg["keep_result_window"] = bool(self.v_keep.get())
        self.on_apply(self.cfg)
        self.win.destroy()


# -------------------------------------------------------------------- main
def selftest():
    """Grab the screen, cut out a piece, OCR it."""
    cfg = load_cfg()
    server = cfg.get("server", DEFAULT_CFG["server"])
    shot = grab_screen()
    print("virtual desktop grab:", shot.size)
    w, h = shot.size
    crop = shot.crop((int(w * 0.1), int(h * 0.1), int(w * 0.5), int(h * 0.3)))
    crop.save(os.path.join(HERE, "_selftest_crop.png"))
    print("crop:", crop.size, "-> _selftest_crop.png")
    t0 = time.time()
    try:
        text, lines, dt = ocr_image(server, crop)
    except Exception as e:  # noqa: BLE001
        print("OCR failed: %r" % e)
        return 2
    print("server=%s  %d lines  %.2fs (round trip %.2fs)"
          % (server, len(lines), dt, time.time() - t0))
    for l in lines:
        print("  %s\t(%.3f)" % (l["text"], l["score"]))
    print("--- clipboard ---")
    print(repr(text))
    return 0


def main():
    global _LOG
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    cfgfile = None
    for i, a in enumerate(args):
        if a == "--log" and i + 1 < len(args):
            cfgfile = args[i + 1]
    app = App()
    if cfgfile:
        app.cfg["log_file"] = cfgfile
        _LOG = cfgfile
    if "--save-dir" in args:
        i = args.index("--save-dir")
        if i + 1 < len(args):
            app.cfg["save_dir"] = args[i + 1]
    if "--shot" in args:
        app.root.after(300, app.start_capture)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
