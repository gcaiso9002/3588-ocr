"""Build the portable single-file executable.

    python make_exe.py

Produces dist\\3588-ocr.exe -- one file, no Python needed on the target
machine, no registry entries, config lives in %APPDATA%\\3588-ocr.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "3588-ocr"
ENTRY = "npu_ocr_gui.py"
ICON = "3588-ocr.ico"

EXCLUDES = [
    "scipy", "matplotlib", "pandas", "notebook", "IPython", "pytest",
    "setuptools", "pip", "wheel", "sqlite3", "test",
    "numpy",           # not used: the OCR runs on the board
]


def main():
    if not os.path.exists(os.path.join(HERE, ICON)):
        subprocess.run([sys.executable, os.path.join(HERE, "make_icon.py")],
                       check=True)
    for junk in ("build", "dist", "%s.spec" % NAME):
        p = os.path.join(HERE, junk)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.isfile(p):
            os.remove(p)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--icon", ICON,
        "--hidden-import", "pystray._win32",
        "--hidden-import", "win32api", "--hidden-import", "win32con",
        "--hidden-import", "win32gui", "--hidden-import", "win32clipboard",
    ]
    for m in EXCLUDES:
        cmd += ["--exclude-module", m]
    cmd.append(ENTRY)
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=HERE, check=True)

    exe = os.path.join(HERE, "dist", NAME + ".exe")
    size = os.path.getsize(exe) / 1024.0 / 1024.0
    print("\nbuilt %s  (%.1f MB)" % (exe, size))


if __name__ == "__main__":
    main()
