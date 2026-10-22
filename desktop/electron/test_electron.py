# -*- coding: utf-8 -*-
"""End-to-end test of the Electron client: Ctrl+1 -> drag -> clipboard.

!!! WARNING !!!
This script will pop up a fullscreen mask on the CURRENT desktop,
covering everything you're working on. Save your work before running it.
If you just want to verify the concurrency logic, use the headless version instead (no popups):
    node test_capture_concurrency.js
"""

"""End-to-end test of the Electron client: Ctrl+1 -> drag -> clipboard.

The driver must be DPI aware, otherwise SetCursorPos coordinates get
virtualised and the selection comes out 1.5x off.
"""
import ctypes
import os
import subprocess
import sys
import time

import win32clipboard
import win32gui

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "electron_test.log")

user32 = ctypes.windll.user32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

# SetCursorPos / GetWindowRect both use the Windows virtual-screen coordinate
# space, so we let Tk place the window and then ask Windows where it really is.
KNOWN = "Hello 12345 ABCdef"
KNOWN_CN = "识别测试 67890"

import tkinter as tk
from PIL import ImageGrab

# virtual screen origin, so we can map GetWindowRect() onto the grab
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
vx = -user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
vy = -user32.GetSystemMetrics(SM_YVIRTUALSCREEN)

root = tk.Tk()
root.geometry("900x400")
tk.Label(root, text=KNOWN, font=("Consolas", 30), bg="white", fg="black").pack(
    fill="both", expand=True)
tk.Label(root, text=KNOWN_CN, font=("Microsoft YaHei UI", 30), bg="white",
         fg="black").pack(fill="both", expand=True)
root.update()

_hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()


def window_is_visible(hwnd):
    """True when our (white) window really is the topmost thing there."""
    from PIL import ImageStat
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    shot = ImageGrab.grab(all_screens=True)
    crop = shot.crop((l - vx + 20, t - vy + 20, l - vx + 220, t - vy + 120))
    # mostly white == our window; the text is black so use the mean, not the min
    return ImageStat.Stat(crop.convert("L")).mean[0] > 170


# Other always-on-top windows (chat apps, the IDE) keep stealing the area,
# so keep raising ours until it really is on top.
for _ in range(12):
    win32gui.SetWindowPos(_hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.update()
    time.sleep(0.35)
    if window_is_visible(_hwnd):
        break
else:
    print("WARNING: the known-text window stays covered")

l, t, r, b = win32gui.GetWindowRect(_hwnd)
WIN_X, WIN_Y, WIN_W, WIN_H = l, t, r - l, b - t
print("known-text window rect:", (l, t, r, b), "| on top:", window_is_visible(_hwnd))

# --- fire the global hotkey (Ctrl+1) -------------------------------------
VK_CONTROL, VK_1 = 0x11, 0x31
user32.keybd_event(VK_CONTROL, 0, 0, 0)
user32.keybd_event(VK_1, 0, 0, 0)
user32.keybd_event(VK_1, 0, 0x0002, 0)
user32.keybd_event(VK_CONTROL, 0, 0x0002, 0)
time.sleep(3.0)

# --- drag a selection over the text --------------------------------------
x0, y0 = WIN_X + 40, WIN_Y + 40
x1, y1 = WIN_X + WIN_W - 40, WIN_Y + WIN_H - 40
user32.SetCursorPos(x0, y0)
time.sleep(0.4)
user32.mouse_event(0x0002, 0, 0, 0, 0)
for i in range(1, 11):
    user32.SetCursorPos(x0 + (x1 - x0) * i // 10, y0 + (y1 - y0) * i // 10)
    time.sleep(0.05)
user32.mouse_event(0x0004, 0, 0, 0, 0)

# --- wait for the OCR round trip -----------------------------------------
ok = False
text = ""
for _ in range(40):
    time.sleep(0.5)
    try:
        win32clipboard.OpenClipboard()
        try:
            text = win32clipboard.GetClipboardData(13)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        continue
    if text and ("12345" in text or "识别测试" in text):
        ok = True
        break

print("clipboard :", repr(text))
print("RESULT    :", "PASS" if ok else "FAIL")
root.destroy()
sys.exit(0 if ok else 1)
