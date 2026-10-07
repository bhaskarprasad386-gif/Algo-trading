#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/Algo-Trading"
SERVICE="algo-telegram-control.service"
SRC="$APP_DIR/deploy/$SERVICE"
DST="/etc/systemd/system/$SERVICE"

[[ "$(id -u)" -eq 0 ]] || { echo "Run as root"; exit 1; }
[[ -f "$SRC" ]] || { echo "$SRC not found"; exit 1; }
[[ -f "$APP_DIR/backend/.env" ]] || { echo "$APP_DIR/backend/.env not found"; exit 1; }

if ! grep -q '^TELEGRAM_BOT_TOKEN=.' "$APP_DIR/backend/.env"; then
  echo "TELEGRAM_BOT_TOKEN is not configured in backend/.env"
  exit 2
fi
if ! grep -q '^TELEGRAM_ADMIN_CHAT_IDS=.' "$APP_DIR/backend/.env"; then
  echo "TELEGRAM_ADMIN_CHAT_IDS is not configured in backend/.env"
  exit 2
fi

install -m 0644 "$SRC" "$DST"
systemctl daemon-reload
systemctl enable --now "$SERVICE"
systemctl --no-pager --full status "$SERVICE"
