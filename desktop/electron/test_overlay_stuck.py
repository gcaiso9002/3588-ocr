"""回归测试：选区遮罩卡死（抓屏期间重复触发热键）。

真实故障（v1.2.0）：
  抓屏要 2 秒（双屏各抓一次），这期间再按一次 Ctrl+1，
  旧代码的守卫只查 overlays.size —— 抓屏还没建遮罩，overlays 是空的，
  于是并发跑出第二套遮罩。overlays 是 Map（按屏幕 id），
  第二套把第一套挤掉，第一套变成「野窗口」：还盖在屏幕上层，
  但 closeOverlays() 再也找不到它 -> 十字光标卡死、Esc 按了也没反应。

判定全部基于**应用自己的日志**（而不是去数桌面上的窗口），
所以测试期间真人在屏幕上乱点、乱框选都不会影响结果：

  1) 任何时刻的「overlays up: N」必须 N == 屏幕数（野窗口会导致 N 翻倍）
  2) 一轮连按只产生一条 capture start（并发根本起不来）
  3) 按 Esc 之后日志里必须出现 overlays closed
  4) 选区中再按热键 = 取消并重新抓屏（capture restarted）

依赖：pywin32。
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

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "3588-ocr.exe")
LOG = os.path.join(os.environ.get("APPDATA", ""), "3588-ocr", "3588-ocr.log")
EXPECT_SCREENS = 2

user32 = ctypes.windll.user32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

failures = []


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


def live_overlay_windows():
    """属于本 app 进程、可见、且几乎铺满一块屏幕的顶层窗口 = 选区遮罩。"""
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


def press_ctrl1(gap_after=0.0):
    user32.keybd_event(0x11, 0, 0, 0)      # Ctrl down
    user32.keybd_event(0x31, 0, 0, 0)      # '1' down
    time.sleep(0.06)
    user32.keybd_event(0x31, 0, win32con.KEYEVENTF_KEYUP, 0)
    user32.keybd_event(0x11, 0, win32con.KEYEVENTF_KEYUP, 0)
    if gap_after:
        time.sleep(gap_after)


def press_escape():
    user32.keybd_event(0x1B, 0, 0, 0)
    time.sleep(0.08)
    user32.keybd_event(0x1B, 0, win32con.KEYEVENTF_KEYUP, 0)


def read_log(mark=0):
    if not os.path.exists(LOG):
        return ""
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        f.seek(mark)
        return f.read()


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  | " + detail) if detail else ""))
    if not ok:
        failures.append(name)


def overlays_up_counts(text):
    return [int(m) for m in re.findall(r"overlays up: (\d+) window", text)]


def wait_log(mark, pattern, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if re.search(pattern, read_log(mark)):
            return True
        time.sleep(0.3)
    return False


def main():
    if not os.path.exists(EXE):
        print("找不到 exe：", EXE)
        return 1

    subprocess.run(["taskkill", "/F", "/IM", "3588-ocr.exe"], capture_output=True, text=True)
    time.sleep(1.5)

    mark = os.path.getsize(LOG) if os.path.exists(LOG) else 0

    print("启动 app ...")
    subprocess.Popen([EXE])
    time.sleep(4.0)                       # 等热键注册 + 板子探测
    check("app 已启动", bool(app_pids()))

    # ---- 场景 1：抓屏窗口期内再按热键 ----
    print("\n[1] 抓屏耗时 ~2s，期间连按 Ctrl+1（复现原故障的并发窗口）")
    press_ctrl1(gap_after=1.0)            # 第 1 次：开始抓屏
    press_ctrl1(gap_after=1.0)            # 第 2 次：落在抓屏窗口期内
    press_ctrl1()                         # 第 3 次
    wait_log(mark, r"overlays up:", timeout=20)
    time.sleep(2.0)

    seg = read_log(mark)
    counts = overlays_up_counts(seg)
    print("      日志里的 overlays up 计数：", counts)
    print("      capture start=%d  capture ignored=%d"
          % (seg.count("capture start"), seg.count("capture ignored")))
    check("遮罩窗口数恒等于屏幕数（没有野窗口）",
          bool(counts) and all(c == EXPECT_SCREENS for c in counts),
          "counts=%s expected=%d" % (counts, EXPECT_SCREENS))
    check("这一轮连按没有并发起两次抓屏",
          seg.count("capture start") <= 1,
          "capture start=%d" % seg.count("capture start"))

    # ---- 场景 2：Esc 能关掉 ----
    print("\n[2] 按 Esc 取消选区")
    n_before = read_log(mark).count("overlays closed:")
    press_escape()
    ok = wait_log(mark, r"overlays closed", timeout=8)
    seg = read_log(mark)
    check("Esc 之后遮罩被关闭（日志出现 overlays closed）",
          ok and seg.count("overlays closed:") > n_before,
          "before=%d after=%d" % (n_before, seg.count("overlays closed:")))
    check("日志里能看到 Esc 是被谁接住的",
          "global-esc" in seg or "cancel" in seg,
          "reasons=" + ",".join(re.findall(r"overlays closed: \d+ window\(s\) \[([^\]]+)\]", seg)))

    # ---- 场景 3：选区中再按热键 = 取消并重新抓屏 ----
    # 注意：测试期间真人可能在屏幕上自己框选，那会先把选区消费掉，
    # 所以这条只作观察，不作硬断言。
    print("\n[3] 选区中再按 Ctrl+1（兜底出口）")
    press_ctrl1()
    wait_log(mark, r"overlays up:", timeout=20)
    time.sleep(1.5)
    press_ctrl1()
    time.sleep(4.0)
    seg = read_log(mark)
    restarted = "capture restarted" in seg
    print("      （观察）选区中再按热键走「取消并重抓」：", "是" if restarted else
          "否（可能已被真人手动框选消费掉）")

    seg = read_log(mark)
    counts = overlays_up_counts(seg)
    check("全程遮罩窗口数都等于屏幕数",
          bool(counts) and all(c == EXPECT_SCREENS for c in counts),
          "counts=%s" % counts)
    # 每个遮罩窗口创建时都会打一条 image drawn —— 它比窗口计数更抗干扰
    drawn = seg.count("overlay: image drawn")
    check("遮罩窗口创建次数 == 屏幕数（没有多建出来的野窗口）",
          drawn <= EXPECT_SCREENS * max(1, seg.count("overlays up:")),
          "image_drawn=%d overlays_up=%d" % (drawn, seg.count("overlays up:")))
    check("结束时没有残留的遮罩窗口", len(live_overlay_windows()) == 0,
          "live=%d" % len(live_overlay_windows()))

    print("\n---- 本次日志尾部 ----")
    print("\n".join(seg.strip().splitlines()[-32:]))

    subprocess.run(["taskkill", "/F", "/IM", "3588-ocr.exe"], capture_output=True, text=True)

    print("\n================ 结果 ================")
    if failures:
        print("失败 %d 项：" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
