@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 取消開機自動啟動，並停止背景中的 N.O.V.A. 核心
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'NOVA Core.lnk'; Remove-Item $lnk -ErrorAction SilentlyContinue;" ^
  "$dir = (Get-Location).Path;" ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like ('*' + $dir + '*service.py*') -or $_.CommandLine -like ('*' + $dir + '*server.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "Write-Host '已取消自動啟動並停止核心'"
pause
