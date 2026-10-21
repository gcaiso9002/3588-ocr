"""Test the packaged exe in a 'cold' environment:

* the config points at a WRONG address (as if the board's IP changed)
* the app must find the board by scanning the local subnet
* the tray icon window must exist
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "3588-ocr.exe")
LOG = os.path.join(HERE, "exe_discover.log")
CFG = os.path.join(os.environ["APPDATA"], "3588-ocr", "npu_ocr_gui_config.json")

user32 = ctypes.windll.user32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

for p in (LOG,):
    if os.path.exists(p):
        os.remove(p)

# point the config at a dead address and wipe the candidates
os.makedirs(os.path.dirname(CFG), exist_ok=True)
cfg = {
    "server": "http://192.168.31.199:8766",     # nothing is there
    "server_candidates": [],
    "hotkey": "ctrl+1",
    "auto_copy": True,
    "auto_paste": False,
    "magnifier": True,
    "keep_result_window": False,
    "log_file": LOG,
}
with open(CFG, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print("config now points at a dead address, candidates cleared")

proc = subprocess.Popen([EXE])
t0 = time.time()
found = None
while time.time() - t0 < 40:
    time.sleep(2)
    try:
        with open(CFG, encoding="utf-8") as f:
            cur = json.load(f)
    except Exception:
        continue
    if cur.get("server") != cfg["server"]:
        found = cur["server"]
        break
print("after %.1fs the config server is:" % (time.time() - t0), found)
if found:
    print("  -> the app found the board by itself:", found)
else:
    print("  -> the app did NOT update the address")
    if os.path.exists(LOG):
        print(open(LOG, encoding="utf-8").read())

# the tray icon window must exist (a onefile exe runs the real UI in a
# CHILD process, so look the windows up by image name, not by pid)
import win32gui
import win32process

pids = set()
try:
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    for line in out.splitlines():
        if '"3588-ocr.exe"' in line:
            pids.add(int(line.split('","')[1]))
except Exception:
    pass
if not pids:
    pids = {proc.pid}
print("\nprocesses named 3588-ocr.exe:", sorted(pids))

found_windows = []


def cb(h, _):
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(h)
    except Exception:
        return
    if pid in pids:
        cls = win32gui.GetClassName(h)
        txt = win32gui.GetWindowText(h)
        found_windows.append((cls, txt, win32gui.IsWindowVisible(h)))


win32gui.EnumWindows(cb, None)
print("windows owned by the exe:")
for cls, txt, vis in found_windows:
    print("   class=%-28s visible=%s text=%r" % (cls, vis, txt))
if not found_windows:
    print("   (none)")

proc.terminate()
try:
    proc.wait(timeout=5)
except Exception:
    proc.kill()
