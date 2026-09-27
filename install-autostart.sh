#!/usr/bin/env bash
# 讓 N.O.V.A. 核心在登入後自動於背景啟動，當掉自動重啟（macOS：launchd；Linux：systemd --user）
# 取消：./install-autostart.sh --uninstall
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
[ -x venv/bin/python ] || ./setup.sh
mkdir -p logs

if [ "$(uname)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.nova.core.plist"
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  if [ "$1" = "--uninstall" ]; then
    rm -f "$PLIST"; echo "已取消自動啟動"; exit 0
  fi
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.nova.core</string>
  <key>ProgramArguments</key>
  <array><string>$DIR/venv/bin/python</string><string>$DIR/service.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/launchd.log</string>
</dict>
</plist>
EOF
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "已設定：登入 Mac 後 N.O.V.A. 核心會自動在背景運作。"
  echo "Mac mini 當伺服器建議：系統設定 → 使用者與群組 → 自動登入；能源 → 防止自動進入睡眠。"
else
  UNIT="$HOME/.config/systemd/user/nova.service"
  if [ "$1" = "--uninstall" ]; then
    systemctl --user disable --now nova.service 2>/dev/null || true
    rm -f "$UNIT"; echo "已取消自動啟動"; exit 0
  fi
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<EOF
[Unit]
Description=N.O.V.A. AI core
[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python $DIR/service.py
Restart=always
[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now nova.service
  echo "已設定：登入後 N.O.V.A. 核心會自動在背景運作。"
fi
echo "網頁：http://localhost:7860  或  https://supercoder592.github.io/websites-/"
