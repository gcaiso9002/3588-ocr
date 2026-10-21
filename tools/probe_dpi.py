"""Parent: creates the overlay exactly like the app, then a child process
moves the mouse so we can compare Tk event coords with physical pixels."""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

user32 = ctypes.windll.user32


def set_dpi_aware():
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "V2"
    except Exception:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "shcore"


print("dpi:", set_dpi_aware())
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

import tkinter as tk

root = tk.Tk()
root.withdraw()
print("virtual:", (vx, vy, vw, vh), " tk screen:",
      root.winfo_screenwidth(), root.winfo_screenheight())

top = tk.Toplevel(root)
top.overrideredirect(True)
top.geometry("%dx%d+%d+%d" % (vw, vh, vx, vy))
top.attributes("-topmost", True)
canvas = tk.Canvas(top, width=vw, height=vh, highlightthickness=0, bd=0,
                    cursor="crosshair")
canvas.pack(fill="both", expand=True)
top.update_idletasks()
top.update()
top.lift()
top.attributes("-topmost", True)
top.focus_force()
top.update()
time.sleep(1.0)

hwnd = int(top.frame(), 16)
r = wt.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(r))
c = wt.RECT()
user32.GetClientRect(hwnd, ctypes.byref(c))
print("window rect: (%d,%d)-(%d,%d)  client: %dx%d  canvas tk: %dx%d  "
      "mapped=%s viewable=%s"
      % (r.left, r.top, r.right, r.bottom, c.right, c.bottom,
         canvas.winfo_width(), canvas.winfo_height(),
         top.winfo_ismapped(), top.winfo_viewable()))

events = []


def on_press(ev):
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    events.append(("press", ev.x, ev.y, p.x, p.y))


def on_motion(ev):
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    events.append(("motion", ev.x, ev.y, p.x, p.y))


canvas.bind("<ButtonPress-1>", on_press)
canvas.bind("<B1-Motion>", on_motion)
top.bind("<ButtonPress-1>", on_press)

DRIVER = r"""
import ctypes, time
u = ctypes.windll.user32
try:
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
u.SetCursorPos(300, 300)
time.sleep(0.6)
u.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.2)
for i in range(1, 9):
    u.SetCursorPos(300 + (1500-300)*i//8, 300 + (700-300)*i//8)
    time.sleep(0.08)
u.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(0.3)
u.SetCursorPos(2000, 1000)
time.sleep(0.3)
u.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.15)
u.mouse_event(0x0004, 0, 0, 0, 0)
"""
print("--- driving the mouse from another process ---")
subprocess.run([sys.executable, "-c", DRIVER])
time.sleep(1.0)
top.update()

print("--- events: kind, ev.x, ev.y | cursor.x, cursor.y ---")
for e in events:
    kind, ex, ey, cx, cy = e
    print("   %-7s ev=(%4d,%4d)  cursor=(%4d,%4d)  ev-cursor=(%4d,%4d)"
          % (kind, ex, ey, cx, cy, ex - cx, ey - cy))
top.destroy()
