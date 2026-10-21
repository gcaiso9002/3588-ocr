"""Deterministic end-to-end test.

A Tk window with known text is placed at a known position; the app is
started; ctrl+1 is fired; a region over that text is dragged; the text is
expected to end up in the clipboard.
"""
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "npu_ocr_gui.py")
LOG = os.path.join(HERE, "_gui_test.log")
CROPS = os.path.join(HERE, "_crops")
if os.path.exists(LOG):
    os.remove(LOG)

user32 = ctypes.windll.user32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

KNOWN = "Hello 12345 ABCdef"
KNOWN_CN = "识别测试 67890"

import tkinter as tk

root = tk.Tk()
root.title("known-text-window")
root.geometry("900x400+600+300")
root.attributes("-topmost", True)
tk.Label(root, text=KNOWN, font=("Consolas", 32),
         bg="white", fg="black").pack(fill="both", expand=True)
tk.Label(root, text=KNOWN_CN, font=("Microsoft YaHei UI", 32),
         bg="white", fg="black").pack(fill="both", expand=True)
root.update()
root.lift()
root.attributes("-topmost", True)

import win32gui

HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
hwnd = int(root.frame(), 16)
win32gui.SetWindowPos(hwnd, HWND_TOPMOST, 600, 300, 900, 400,
                       SWP_NOMOVE | SWP_NOSIZE)
root.update()
time.sleep(1.5)

from PIL import ImageGrab

vx = user32.GetSystemMetrics(76)
vy = user32.GetSystemMetrics(77)
probe = ImageGrab.grab(all_screens=True)
probe.crop((600 - vx, 300 - vy, 1500 - vx, 700 - vy)).save(
    os.path.join(HERE, "_probe_window.png"))
print("known-text window at 600,400 900x260; probe saved")

proc = subprocess.Popen([sys.executable, APP, "--log", LOG,
                         "--save-dir", CROPS],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, creationflags=0x08000000)
time.sleep(4)
if proc.poll() is not None:
    print("app died:", proc.stdout.read())
    sys.exit(1)

VK_CONTROL, VK_1 = 0x11, 0x31
user32.keybd_event(VK_CONTROL, 0, 0, 0)
user32.keybd_event(VK_1, 0, 0, 0)
user32.keybd_event(VK_1, 0, 0x0002, 0)
user32.keybd_event(VK_CONTROL, 0, 0x0002, 0)
time.sleep(2.5)

x0, y0 = 610, 310
x1, y1 = 1480, 690
print("dragging %d,%d -> %d,%d" % (x0, y0, x1, y1))
user32.SetCursorPos(x0, y0)
time.sleep(0.4)
user32.mouse_event(0x0002, 0, 0, 0, 0)
for i in range(1, 11):
    user32.SetCursorPos(x0 + (x1 - x0) * i // 10, y0 + (y1 - y0) * i // 10)
    time.sleep(0.05)
user32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(5)

print("--- app log ---")
try:
    with open(LOG, "r", encoding="utf-8") as f:
        print(f.read())
except Exception as e:
    print("no log:", e)

print("--- clipboard ---")
try:
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        txt = win32clipboard.GetClipboardData(13)
    finally:
        win32clipboard.CloseClipboard()
    print("clipboard :", repr(txt))
    print("expected  :", repr(KNOWN), repr(KNOWN_CN))
    ok = "12345" in txt and "ABCdef" in txt and "识别测试" in txt
    print("RESULT    :", "PASS" if ok else "FAIL")
except Exception as e:
    print("clipboard read failed:", e)

proc.terminate()
try:
    proc.wait(timeout=5)
except Exception:
    proc.kill()
root.destroy()

