#!/usr/bin/env bash
set -Eeuo pipefail

# Install/update ONLY the independent diagnostics collector.
# This deliberately does not fetch/merge code, install backend dependencies,
# or restart/reload algo-trading.service, broker sockets, or market-data runners.

APP_DIR="${APP_DIR:-/opt/Algo-Trading}"
SERVICE_NAME="algo-diagnostics.service"
SERVICE_SRC="$APP_DIR/deploy/$SERVICE_NAME"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
AGENT="$APP_DIR/deploy/server_diagnostics_agent.py"
HEALTH_URL="http://127.0.0.1:8765/health"

log() { printf '\n[algo-diagnostics] %s\n' "$*"; }
die() { printf '\n[algo-diagnostics] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run this script as root."
[[ -f "$SERVICE_SRC" ]] || die "$SERVICE_SRC not found."
[[ -f "$AGENT" ]] || die "$AGENT not found."
command -v python3 >/dev/null 2>&1 || die "python3 is required."
command -v systemctl >/dev/null 2>&1 || die "systemd is required."
command -v curl >/dev/null 2>&1 || die "curl is required."

log "Installing independent diagnostics service files"
install -m 0644 "$SERVICE_SRC" "$SERVICE_DST"
chmod 0755 "$AGENT"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

healthy=0
for attempt in {1..15}; do
  if curl --fail --silent --connect-timeout 1 --max-time 2 "$HEALTH_URL" >/dev/null; then
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" -ne 1 ]]; then
  systemctl --no-pager --full status "$SERVICE_NAME" || true
  journalctl -u "$SERVICE_NAME" -n 40 --no-pager || true
  die "Diagnostics collector did not become healthy."
fi

log "Diagnostics collector is healthy"
systemctl is-active "$SERVICE_NAME"
curl --fail --silent "$HEALTH_URL"
printf '\n'
log "No algo-trading.service restart/reload or broker/runner lifecycle action was performed."
