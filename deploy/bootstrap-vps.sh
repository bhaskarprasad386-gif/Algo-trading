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

log "Removing retired independent IV master recovery timer and credential"
systemctl disable --now algo-trading-master-recovery.timer 2>/dev/null || true
systemctl stop algo-trading-master-recovery.service 2>/dev/null || true
rm -f /etc/systemd/system/algo-trading-master-recovery.service
rm -f /etc/systemd/system/algo-trading-master-recovery.timer
rm -f /etc/algo-trading/master-recovery.env
rmdir /etc/algo-trading 2>/dev/null || true
systemctl daemon-reload
systemctl reset-failed algo-trading-master-recovery.service 2>/dev/null || true

log "Checking repository state"
git diff --quiet || die "Working tree has uncommitted changes; refusing to overwrite them."
git diff --cached --quiet || die "Index has staged changes; refusing to continue."

log "Syncing main from GitHub"
git fetch origin main
git merge --ff-only origin/main

log "Installing OS prerequisites"
export DEBIAN_FRONTEND=noninteractive

# Another unattended-upgrades or apt process may temporarily own the package locks.
# Wait boundedly; never kill the lock owner or delete lock files.
wait_for_apt_locks() {
  local timeout_seconds="${1:-300}" elapsed=0 lock
  local locks=(/var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock)
  while :; do
    local busy=false
    for lock in "${locks[@]}"; do
      if command -v fuser >/dev/null 2>&1 && fuser "$lock" >/dev/null 2>&1; then
        busy=true
        break
      fi
    done
    [[ "$busy" == false ]] && return 0
    if (( elapsed >= timeout_seconds )); then
      die "APT/DPKG locks remained busy after ${timeout_seconds}s; no package process was killed."
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
}

apt_with_lock_retry() {
  local attempt
  for attempt in 1 2 3; do
    wait_for_apt_locks 300
    if "$@"; then
      return 0
    fi
    log "APT command failed (attempt ${attempt}/3); waiting before safe retry"
    sleep $((attempt * 10))
  done
  die "APT command failed after 3 attempts: $*"
}

apt_with_lock_retry apt-get update -y
apt_with_lock_retry apt-get install -y git python3 python3-venv python3-pip curl sqlite3 ca-certificates

log "Configuring VPS virtual memory"
bash "$APP_DIR/deploy/configure-vps-swap.sh"

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

log "Installing independent VPS diagnostics collector"
DIAGNOSTICS_SERVICE_SRC="$APP_DIR/deploy/algo-diagnostics.service"
DIAGNOSTICS_SERVICE_DST="/etc/systemd/system/algo-diagnostics.service"
DIAGNOSTICS_AGENT="$APP_DIR/deploy/server_diagnostics_agent.py"
[[ -f "$DIAGNOSTICS_SERVICE_SRC" ]] || die "$DIAGNOSTICS_SERVICE_SRC not found."
[[ -f "$DIAGNOSTICS_AGENT" ]] || die "$DIAGNOSTICS_AGENT not found."
install -m 0644 "$DIAGNOSTICS_SERVICE_SRC" "$DIAGNOSTICS_SERVICE_DST"
chmod 0644 "$DIAGNOSTICS_AGENT"
systemctl daemon-reload
systemctl enable algo-diagnostics.service
systemctl restart algo-diagnostics.service

diagnostics_healthy=0
for attempt in {1..15}; do
  if curl --fail --silent --show-error --connect-timeout 1 --max-time 2 http://127.0.0.1:8765/health >/tmp/algo-diagnostics-health.json 2>/dev/null; then
    diagnostics_healthy=1
    break
  fi
  sleep 1
done
if [[ "$diagnostics_healthy" -ne 1 ]]; then
  log "Independent diagnostics collector failed health verification:"
  systemctl --no-pager --full status algo-diagnostics.service || true
  journalctl -u algo-diagnostics.service -n 40 --no-pager || true
  rm -f /tmp/algo-diagnostics-health.json
  die "Independent VPS diagnostics collector did not become healthy."
fi
rm -f /tmp/algo-diagnostics-health.json

# Optional Telegram control bot: install/start only when credentials are already configured.
TELEGRAM_INSTALLER="$APP_DIR/deploy/install-telegram-control-bot.sh"
if [[ -x "$TELEGRAM_INSTALLER" && -f "$BACKEND_DIR/.env" ]] && grep -q '^TELEGRAM_BOT_TOKEN=.' "$BACKEND_DIR/.env" && grep -q '^TELEGRAM_ADMIN_CHAT_IDS=.' "$BACKEND_DIR/.env"; then
  bash "$TELEGRAM_INSTALLER"
else
  log "Telegram control bot not configured; leaving it disabled"
fi

log "Waiting for local API health"
healthy=0
for attempt in {1..180}; do
  if curl --fail --silent --show-error --connect-timeout 2 --max-time 10 http://127.0.0.1:8000/health >/tmp/algo-health.json 2>/tmp/algo-health-error; then
    healthy=1
    break
  fi
  if (( attempt % 15 == 0 )); then
    log "Health check still waiting (attempt $attempt/180)"
    systemctl is-active "$SERVICE_NAME" || true
    tail -5 /tmp/algo-health-error 2>/dev/null || true
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
