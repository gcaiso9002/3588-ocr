# 3588-ocr —— 前台窗口 / 模拟粘贴 小助手
#   -Mode capture          打印当前前台窗口句柄
#   -Mode paste -Hwnd N   把焦点还给 N 号窗口并发送 Ctrl+V
# 纯 ASCII，避免 PowerShell 5.1 按 ANSI 读中文出乱码。

param(
  [string]$Mode = 'capture',
  [long]$Hwnd = 0
)

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class NpuOcrWin {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, IntPtr extra);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

if ($Mode -eq 'capture') {
  $h = [NpuOcrWin]::GetForegroundWindow()
  if ($h -ne [IntPtr]::Zero) { Write-Output $h.ToInt64() }
  exit 0
}

if ($Mode -eq 'paste' -and $Hwnd -ne 0) {
  $h = [IntPtr]$Hwnd
  if ([NpuOcrWin]::IsIconic($h)) {
    [void][NpuOcrWin]::ShowWindow($h, 9)   # SW_RESTORE
    Start-Sleep -Milliseconds 220
  }
  [void][NpuOcrWin]::BringWindowToTop($h)
  [void][NpuOcrWin]::SetForegroundWindow($h)
  Start-Sleep -Milliseconds 260

  $VK_CONTROL = 0x11
  $VK_V = 0x56
  [NpuOcrWin]::keybd_event($VK_CONTROL, 0, 0, [IntPtr]::Zero)
  [NpuOcrWin]::keybd_event($VK_V, 0, 0, [IntPtr]::Zero)
  [NpuOcrWin]::keybd_event($VK_V, 0, 2, [IntPtr]::Zero)
  [NpuOcrWin]::keybd_event($VK_CONTROL, 0, 2, [IntPtr]::Zero)
  exit 0
}

exit 0
