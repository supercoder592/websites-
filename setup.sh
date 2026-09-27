#!/usr/bin/env bash
# N.O.V.A. 安裝（macOS / Linux）
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}

if [ ! -d venv ]; then
  "$PY" -m venv venv || { echo "找不到 Python 3，請先安裝（macOS 可用 brew install python）"; exit 1; }
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
echo "安裝完成！執行 ./start.sh 啟動 N.O.V.A."
