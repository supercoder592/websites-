#!/usr/bin/env bash
# macOS：在 Finder 雙擊這個檔案就會開終端機啟動 N.O.V.A.
cd "$(dirname "$0")"
chmod +x setup.sh start.sh 2>/dev/null
exec ./start.sh
