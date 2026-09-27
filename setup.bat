@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== N.O.V.A. 安裝 ====
set PIP_CACHE_DIR=%~dp0.pipcache

if not exist venv (
  python -m venv venv || (echo 找不到 Python，請先安裝 Python 3.10 以上 & pause & exit /b 1)
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip

where nvidia-smi >nul 2>nul
if %errorlevel%==0 (
  echo 偵測到 NVIDIA 顯卡，安裝 CUDA 版 PyTorch
  pip install torch --index-url https://download.pytorch.org/whl/cu124
) else (
  echo 沒有 NVIDIA 顯卡，安裝 CPU 版 PyTorch
  pip install torch --index-url https://download.pytorch.org/whl/cpu
)
pip install -r requirements.txt

where ollama >nul 2>nul
if %errorlevel% neq 0 (
  echo.
  echo [注意] 尚未安裝 Ollama，聊天/寫程式需要它：https://ollama.com/download
) else (
  ollama list | findstr /i "qwen" >nul || ollama pull qwen3-vl:4b-instruct
)
echo.
echo 安裝完成！執行 start.bat 啟動 N.O.V.A.
pause
