@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 讓 N.O.V.A. 核心在登入 Windows 後自動於背景啟動（不需要系統管理員權限）
if not exist venv\Scripts\pythonw.exe (
  echo 尚未安裝，先執行 setup.bat
  call setup.bat
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dir = (Get-Location).Path;" ^
  "$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'NOVA Core.lnk';" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
  "$s.TargetPath = Join-Path $dir 'venv\Scripts\pythonw.exe';" ^
  "$s.Arguments = '\"' + (Join-Path $dir 'service.py') + '\"';" ^
  "$s.WorkingDirectory = $dir; $s.Description = 'N.O.V.A. AI core'; $s.WindowStyle = 7; $s.Save();" ^
  "Start-Process -FilePath $s.TargetPath -ArgumentList $s.Arguments -WorkingDirectory $dir -WindowStyle Hidden;" ^
  "Write-Host ('已設定開機自動啟動：' + $lnk)"
echo.
echo 完成！之後只要登入 Windows，AI 核心就會在背景自動運作（當掉也會自動重啟）。
echo 網頁：http://localhost:7860  或  https://supercoder592.github.io/websites-/
echo 取消自動啟動：執行 uninstall-autostart.bat
pause
