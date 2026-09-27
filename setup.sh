#!/usr/bin/env bash
# N.O.V.A. 安裝（macOS / Linux）
set -e
cd "$(dirname "$0")"

# 找 Python 3.10 以上（macOS 內建的 3.9 太舊，新版 transformers 不支援）
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY=$c; break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "需要 Python 3.10 以上。macOS 請先執行：brew install python@3.12"
  echo "（沒有 Homebrew 的話先到 https://brew.sh 安裝，或從 https://www.python.org 下載）"
  exit 1
fi
echo "使用 $($PY --version)"

if [ ! -d venv ]; then
  "$PY" -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip
# macOS 的 PyTorch 已內建 Apple Silicon GPU（MPS）支援；Linux 預設版本內建 NVIDIA CUDA
pip install torch
pip install -r requirements.txt

if command -v ollama >/dev/null 2>&1; then
  ollama list | grep -qi qwen || ollama pull qwen3-vl:4b-instruct
else
  echo ""
  echo "[注意] 尚未安裝 Ollama，聊天/寫程式需要它：https://ollama.com/download"
fi
echo ""
echo "安裝完成！執行 ./start.sh（或在 Finder 雙擊 start.command）啟動 N.O.V.A."
