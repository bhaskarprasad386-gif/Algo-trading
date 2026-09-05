#!/usr/bin/env bash
set -Eeuo pipefail

# Safe, repeatable VPS bootstrap for Algo Trading.
# - Preserves backend/.env and the existing SQLite database.
# - Does not enable live trading.
# - Does not change firewall or SSH configuration.
# - Intended for Ubuntu 24.04+ with systemd.

APP_DIR="/opt/Algo-Trading"
BACKEND_DIR="$APP_DIR/backend"
VENV_DIR="$APP_DIR/.venv"
SERVICE_NAME="algo-trading.service"
SERVICE_SRC="$APP_DIR/deploy/$SERVICE_NAME"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"

log() { printf '\n[algo-vps] %s\n' "$*"; }
die() { printf '\n[algo-vps] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run this script as root."
[[ -d "$APP_DIR/.git" ]] || die "$APP_DIR is not a Git repository. Clone the repository there first."
[[ -f "$BACKEND_DIR/requirements.txt" ]] || die "backend/requirements.txt not found."
[[ -f "$SERVICE_SRC" ]] || die "$SERVICE_SRC not found."

cd "$APP_DIR"

log "Checking repository state"
git diff --quiet || die "Working tree has uncommitted changes; refusing to overwrite them."
git diff --cached --quiet || die "Index has staged changes; refusing to continue."

log "Syncing main from GitHub"
git fetch origin main
git merge --ff-only origin/main

log "Installing OS prerequisites"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git python3 python3-venv python3-pip curl sqlite3 ca-certificates

log "Creating/updating Python virtual environment"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip wheel
"$VENV_DIR/bin/pip" install -r "$BACKEND_DIR/requirements.txt"

# Never create, replace, print, or modify backend/.env here.
if [[ -f "$BACKEND_DIR/.env" ]]; then
  log "Existing backend/.env preserved"
else
  log "No backend/.env found. Service will start only if the application configuration permits it."
fi

log "Installing systemd service"
install -m 0644 "$SERVICE_SRC" "$SERVICE_DST"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

log "Waiting for local API health"
healthy=0
for _ in {1..20}; do
  if curl --fail --silent --show-error --max-time 3 http://127.0.0.1:8000/health >/tmp/algo-health.json 2>/dev/null; then
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" -ne 1 ]]; then
  log "Service did not become healthy. Recent service status:"
  systemctl --no-pager --full status "$SERVICE_NAME" || true
  log "Recent logs:"
  journalctl -u "$SERVICE_NAME" -n 60 --no-pager || true
  rm -f /tmp/algo-health.json
  exit 1
fi

log "Deployment verification"
printf 'commit: '
git rev-parse --short HEAD
printf 'service: '
systemctl is-active "$SERVICE_NAME"
printf 'health: '
cat /tmp/algo-health.json
rm -f /tmp/algo-health.json

log "VPS backend deployment complete. Live trading remains OFF."
