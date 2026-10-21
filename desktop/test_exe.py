"""End-to-end test of the packaged single-file exe."""
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "3588-ocr.exe")
LOG = os.path.join(HERE, "exe_test.log")
CROPS = os.path.join(HERE, "exe_crops")

for p in (LOG,):
    if os.path.exists(p):
        os.remove(p)

user32 = ctypes.windll.user32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

print("exe:", EXE, "%.1f MB" % (os.path.getsize(EXE) / 1024.0 / 1024.0))
print("starting it ...")
proc = subprocess.Popen([EXE, "--log", LOG, "--save-dir", CROPS])
time.sleep(8)

if proc.poll() is not None:
    print("the exe died immediately, exit code", proc.returncode)
    if os.path.exists(LOG):
        print(open(LOG, encoding="utf-8").read())
    sys.exit(1)

print("running, pid", proc.pid)
print("--- log after start ---")
print(open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else "(no log)")

cfg = os.path.join(os.environ["APPDATA"], "3588-ocr", "npu_ocr_gui_config.json")
print("--- config written to %APPDATA% ---")
print(open(cfg, encoding="utf-8").read() if os.path.exists(cfg) else "(no config yet)")

# fire ctrl+1 and drag over a known window
import tkinter as tk

KNOWN = "Hello 12345 ABCdef"
KNOWN_CN = "识别测试 67890"
root = tk.Tk()
root.geometry("900x400+600+300")
root.attributes("-topmost", True)
tk.Label(root, text=KNOWN, font=("Consolas", 32), bg="white",
         fg="black").pack(fill="both", expand=True)
tk.Label(root, text=KNOWN_CN, font=("Microsoft YaHei UI", 32), bg="white",
         fg="black").pack(fill="both", expand=True)
root.update()
root.lift()
root.attributes("-topmost", True)
import win32gui
win32gui.SetWindowPos(int(root.frame(), 16), -1, 600, 300, 900, 400, 0x0003)
root.update()
time.sleep(1.5)

VK_CONTROL, VK_1 = 0x11, 0x31
user32.keybd_event(VK_CONTROL, 0, 0, 0)
user32.keybd_event(VK_1, 0, 0, 0)
user32.keybd_event(VK_1, 0, 0x0002, 0)
user32.keybd_event(VK_CONTROL, 0, 0x0002, 0)
time.sleep(3)

x0, y0, x1, y1 = 610, 310, 1480, 690
user32.SetCursorPos(x0, y0)
time.sleep(0.4)
user32.mouse_event(0x0002, 0, 0, 0, 0)
for i in range(1, 11):
    user32.SetCursorPos(x0 + (x1 - x0) * i // 10, y0 + (y1 - y0) * i // 10)
    time.sleep(0.05)
user32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(6)

print("--- log after the capture ---")
print(open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else "(no log)")

print("--- clipboard ---")
try:
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        txt = win32clipboard.GetClipboardData(13)
    finally:
        win32clipboard.CloseClipboard()
    print("clipboard :", repr(txt))
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
