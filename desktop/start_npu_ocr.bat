@echo off
REM Start the npu-ocr screenshot-OCR tool in the background (no console window).
cd /d "%~dp0"
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw npu_ocr_gui.py
) else (
    start "" python npu_ocr_gui.py
)
echo npu-ocr started.  Press Ctrl+1 to capture and recognise text.
