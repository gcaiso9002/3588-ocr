# -*- coding: utf-8 -*-
"""Self-check: auto-discover the board on the LAN.

!!! WARNING !!!
This script will pop up a fullscreen mask on the CURRENT desktop,
covering everything you're working on. Save your work before running it.
If you just want to verify the concurrency logic, use the headless version instead (no popups):
    node test_capture_concurrency.js
"""

"""Auto-discovery test: point the config at a dead address, expect the app to
find the board on its own and write the working address back.

    python test_discover.py [path-to-exe]
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_DIR = os.path.join(os.environ["APPDATA"], "3588-ocr")
CFG = os.path.join(CFG_DIR, "config.json")
LOG = os.path.join(CFG_DIR, "3588-ocr.log")
DEAD = "http://192.168.31.199:8766"      # nothing listens there


def kill_running():
    subprocess.run(["taskkill", "/IM", "3588-ocr.exe", "/F"],
                   capture_output=True)
    subprocess.run(["taskkill", "/IM", "electron.exe", "/F"],
                   capture_output=True)
    time.sleep(2)


def read_cfg():
    with open(CFG, encoding="utf-8-sig") as f:
        return json.load(f)


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "dist", "3588-ocr.exe")
    kill_running()

    cfg = {
        "server": DEAD,
        "server_candidates": [],
        "hotkey": "ctrl+1",
        "auto_copy": True,
        "auto_paste": False,
        "magnifier": True,
        "keep_result_window": False,
        "merge_lines": False,
        "trim_spaces": False,
        "show_toolbar": True,
        "start_minimized": True,
        "ocr_timeout": 60,
    }
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("config set to the dead address", DEAD)

    subprocess.Popen([exe])
    found = None
    for _ in range(60):
        time.sleep(1)
        try:
            now = read_cfg().get("server")
        except Exception:
            continue
        if now and now != DEAD:
            found = now
            break

    print("config now points at :", found)
    if found:
        try:
            with open(LOG, encoding="utf-8") as f:
                tail = [l for l in f.read().splitlines() if "found an npu-ocr" in l]
            print("log says             :", tail[-1] if tail else "(no line)")
        except Exception:
            pass
    print("RESULT    :", "PASS" if found else "FAIL")
    kill_running()
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
