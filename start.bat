@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist venv (
  echo 第一次使用，先執行安裝…
  call setup.bat
)

rem 確保 Ollama 在背景運作
curl -s http://127.0.0.1:11434/api/tags >nul 2>nul || (
  where ollama >nul 2>nul && start "" /min ollama serve
)

start "" http://localhost:7860
venv\Scripts\python.exe server.py
pause
