# -*- coding: utf-8 -*-
"""A/B compare: v1.2.0 (buggy) vs v1.2.1 (fixed).

!!! WARNING !!!
This script will pop up a fullscreen mask on the CURRENT desktop,
covering everything you're working on. Save your work before running it.
If you just want to verify the concurrency logic, use the headless version instead (no popups):
    node test_capture_concurrency.js
"""

"""A/B 对比：v1.2.0（旧）vs v1.2.1（新）在「抓屏期间连按热键」下的表现。

判据：**"overlay: image drawn" 的条数**。
每创建一个选区遮罩窗口，渲染层就会打一条 image drawn。
双屏 = 一套遮罩 = 2 条。旧版并发会建出两套 = 4 条。

用这个信号而不是「当前可见窗口数」，是因为测试期间真人可能在屏幕上
自己框选，会把窗口提前消费掉；而已创建的窗口数量不会变。

用法：python test_overlay_ab.py <旧版exe> <新版exe>
"""
import ctypes
import os
import re
import subprocess
import sys
import time

import win32con
import win32gui
import win32process

LOG = os.path.join(os.environ.get("APPDATA", ""), "3588-ocr", "3588-ocr.log")
EXPECT_SCREENS = 2

user32 = ctypes.windll.user32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)


def press_ctrl1(gap_after=0.0):
    user32.keybd_event(0x11, 0, 0, 0)
    user32.keybd_event(0x31, 0, 0, 0)
    time.sleep(0.06)
    user32.keybd_event(0x31, 0, win32con.KEYEVENTF_KEYUP, 0)
    user32.keybd_event(0x11, 0, win32con.KEYEVENTF_KEYUP, 0)
    if gap_after:
        time.sleep(gap_after)


def read_log(mark=0):
    if not os.path.exists(LOG):
        return ""
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        f.seek(mark)
        return f.read()


def app_pids():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq 3588-ocr.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def live_overlays():
    pids = app_pids()
    if not pids:
        return []
    found = []

    def cb(hwnd, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if pid not in pids or not user32.IsWindowVisible(hwnd):
            return
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        if (r - l) >= 800 and (b - t) >= 600:
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found


def run_case(label, exe):
    print("\n" + "=" * 58)
    print("用例：", label)
    print("=" * 58)
    subprocess.run(["taskkill", "/F", "/IM", "3588-ocr.exe"], capture_output=True, text=True)
    time.sleep(2.0)

    mark = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    subprocess.Popen([exe])
    time.sleep(4.5)

    # 连按 3 次，间隔 1s —— 正好落在 ~2s 的抓屏窗口期里
    press_ctrl1(gap_after=1.0)
    press_ctrl1(gap_after=1.0)
    press_ctrl1()
    time.sleep(6.0)

    seg = read_log(mark)
    drawn = seg.count("overlay: image drawn")
    starts = seg.count("capture start")
    live = len(live_overlays())

    print("  capture start 次数        : %d" % starts)
    print("  遮罩窗口创建数(image drawn): %d   (一套 = %d)" % (drawn, EXPECT_SCREENS))
    print("  当前仍可见的遮罩窗口数     : %d" % live)
    print("  结论                      : %s" % (
        "野窗口！多建出了 %d 个关不掉的遮罩" % (drawn - EXPECT_SCREENS)
        if drawn > EXPECT_SCREENS else
        "正常，一套遮罩 = %d 个窗口" % EXPECT_SCREENS))

    # 再测 Esc 能不能脱困
    before = read_log(mark).count("overlays closed:")
    user32.keybd_event(0x1B, 0, 0, 0)
    time.sleep(0.08)
    user32.keybd_event(0x1B, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(2.0)
    after = read_log(mark).count("overlays closed:")
    esc_ok = after > before
    live_after = len(live_overlays())
    print("  按 Esc 后：关闭记录 %d -> %d   残留可见遮罩 %d   结论: %s" % (
        before, after, live_after,
        "Esc 有效" if esc_ok else ("无法脱困，野窗口 %d 个仍在" % live_after
                                   if live_after else "无残留(可能已被别的路径关掉)")))

    subprocess.run(["taskkill", "/F", "/IM", "3588-ocr.exe"], capture_output=True, text=True)
    time.sleep(1.0)
    return drawn, live_after, esc_ok


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    old_exe, new_exe = sys.argv[1], sys.argv[2]
    for p in (old_exe, new_exe):
        if not os.path.exists(p):
            print("找不到 exe：", p)
            return 1

    old = run_case("v1.2.0 旧版（有 bug）", old_exe)
    new = run_case("v1.2.1 新版（已修）", new_exe)

    print("\n" + "=" * 58)
    print("A/B 汇总")
    print("=" * 58)
    print("  旧版：遮罩窗口创建 %d 个，Esc 后残留 %d 个，Esc 生效=%s" % old)
    print("  新版：遮罩窗口创建 %d 个，Esc 后残留 %d 个，Esc 生效=%s" % new)
    ok = (new[0] <= EXPECT_SCREENS and new[1] == 0)
    print("\n  " + ("新版通过：不再多建野窗口，Esc 能脱困" if ok else "新版仍有问题"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
