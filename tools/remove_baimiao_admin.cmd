@echo off
REM Remove BaiMiao Desktop (白描桌面版) completely.
REM Right-click this file -> "Run as administrator".
setlocal

set DIR=C:\Program Files\白描桌面版
set UNINST=%DIR%\uninstall.exe

echo === stopping 白描桌面版 ===
taskkill /IM 白描桌面版.exe /F >nul 2>&1
taskkill /IM screencapture.exe /F >nul 2>&1
timeout /t 2 >nul

if exist "%UNINST%" (
    echo === running the official uninstaller ===
    start /wait "" "%UNINST%" /S
    timeout /t 5 >nul
)

if exist "%DIR%" (
    echo === removing the install folder ===
    rmdir /s /q "%DIR%"
)

echo === removing the registry entry ===
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" /f /v "白描桌面版" >nul 2>&1
for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall" /s /f "白描桌面版" 2^>nul ^| find "HKEY"') do (
    reg delete "%%A" /f >nul 2>&1
)
reg delete "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /f /v "白描桌面版" >nul 2>&1

echo.
echo === done ===
if exist "%DIR%" (echo   WARNING: %DIR% still exists) else (echo   install folder removed)
echo   You can now use Ctrl+1 for the npu-ocr screenshot tool.
pause
