#!/usr/bin/env bash
# N.O.V.A. 啟動（macOS / Linux）
cd "$(dirname "$0")"
[ -d venv ] || ./setup.sh

# 確保 Ollama 在背景運作
if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  command -v ollama >/dev/null 2>&1 && (ollama serve >/dev/null 2>&1 &)
fi

PORT=${NOVA_PORT:-7860}
( sleep 3; (command -v open >/dev/null && open "http://localhost:$PORT") || (command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT") ) >/dev/null 2>&1 &
exec venv/bin/python server.py
