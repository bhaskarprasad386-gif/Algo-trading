#!/usr/bin/env bash
# Install a least-privilege daily systemd recovery job. This does not restart
# algo-trading.service, alter scanner state, or touch WebSocket processes.
set -Eeuo pipefail

REPO_DIR="${REPO_DIR:-/root/Algo-trading}"
ENV_FILE="/etc/algo-trading/master-recovery.env"
SERVICE_FILE="/etc/systemd/system/algo-trading-master-recovery.service"
TIMER_FILE="/etc/systemd/system/algo-trading-master-recovery.timer"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash backend/scripts/install_master_recovery_timer.sh" >&2
  exit 2
fi
if [[ ! -d "$REPO_DIR/backend" ]]; then
  echo "Backend directory not found: $REPO_DIR/backend" >&2
  exit 2
fi
if [[ "$REPO_DIR" == *" "* ]]; then
  echo "Repository path must not contain spaces for this systemd unit." >&2
  exit 2
fi

BACKEND_DIR="$REPO_DIR/backend"
if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_PATH="$PYTHON_BIN"
elif [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  PYTHON_PATH="$BACKEND_DIR/.venv/bin/python"
elif [[ -x "$BACKEND_DIR/venv/bin/python" ]]; then
  PYTHON_PATH="$BACKEND_DIR/venv/bin/python"
elif [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
  PYTHON_PATH="$REPO_DIR/.venv/bin/python"
elif [[ -x "$REPO_DIR/venv/bin/python" ]]; then
  PYTHON_PATH="$REPO_DIR/venv/bin/python"
else
  PYTHON_PATH="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON_PATH" || ! -x "$PYTHON_PATH" ]]; then
  echo "Python interpreter not found; set PYTHON_BIN to the backend virtualenv interpreter." >&2
  exit 2
fi
if [[ "$PYTHON_PATH" == *" "* ]]; then
  echo "Python interpreter path must not contain spaces." >&2
  exit 2
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cat >&2 <<EOF
Missing GitHub Actions read-only credential file: $ENV_FILE

Create it securely (use a fine-grained token limited to this repository with
Actions: read permission only):
  sudo install -d -m 700 /etc/algo-trading
  sudo bash -c 'umask 077; read -rsp "GitHub Actions-read token: " t; echo; printf "GH_TOKEN=%s\\n" "$t" > /etc/algo-trading/master-recovery.env"'
  sudo chmod 600 "$ENV_FILE"

Then rerun this installer. Never paste the token into chat or commit it.
EOF
  exit 2
fi
if [[ "$(stat -c '%a' "$ENV_FILE")" != "600" || "$(stat -c '%u' "$ENV_FILE")" != "0" ]]; then
  echo "$ENV_FILE must be root-owned with mode 600." >&2
  exit 2
fi
if ! grep -Eq '^(GITHUB_TOKEN|GH_TOKEN)=.+' "$ENV_FILE"; then
  echo "$ENV_FILE must contain GITHUB_TOKEN=... or GH_TOKEN=..." >&2
  exit 2
fi

mkdir -p "$BACKEND_DIR/data"

# Verify imports before installing any systemd files.
(
  cd "$BACKEND_DIR"
  PYTHONPATH="$BACKEND_DIR" "$PYTHON_PATH" -c \
    'import requests; from app.market_data import box_spread_instrument_cache; from app.scanner.iv_skew_flip import instruments'
)

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Recover validated Box Spread instrument-master artifact from GitHub Actions
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=root
Group=root
UMask=0077
WorkingDirectory=$BACKEND_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONPATH=$BACKEND_DIR
ExecStart=$PYTHON_PATH $BACKEND_DIR/scripts/recover_latest_box_spread_master_artifact.py
TimeoutStartSec=240
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=$BACKEND_DIR/data
EOF

cat > "$TIMER_FILE" <<'EOF'
[Unit]
Description=Daily validated instrument-master recovery after GitHub cache refresh

[Timer]
# 00:45 UTC = 06:15 IST; both scheduled GitHub cache workflows run by 00:05 UTC.
OnCalendar=*-*-* 00:45:00 UTC
Persistent=true
RandomizedDelaySec=5m
Unit=algo-trading-master-recovery.service

[Install]
WantedBy=timers.target
EOF

chmod 644 "$SERVICE_FILE" "$TIMER_FILE"
systemd-analyze verify "$SERVICE_FILE" "$TIMER_FILE"
systemctl daemon-reload
systemctl enable --now algo-trading-master-recovery.timer

echo "Installed daily recovery timer. No trading service was restarted."
systemctl --no-pager --full status algo-trading-master-recovery.timer
echo "Run one controlled recovery now with:"
echo "  systemctl start algo-trading-master-recovery.service"
echo "Inspect result with:"
echo "  journalctl -u algo-trading-master-recovery.service -n 100 --no-pager"
