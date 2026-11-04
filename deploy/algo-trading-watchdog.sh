#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="algo-trading.service"
HEALTH_URL="http://127.0.0.1:8000/health"
STATE_DIR="/run/algo-trading-watchdog"
FAIL_FILE="$STATE_DIR/consecutive_failures"
MAX_FAILURES=3

mkdir -p "$STATE_DIR"

service_active="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
if [[ "$service_active" != "active" ]]; then
  echo "WATCHDOG_SERVICE_INACTIVE state=$service_active"
  systemctl reset-failed "$SERVICE_NAME" || true
  systemctl start "$SERVICE_NAME"
  printf '0\n' > "$FAIL_FILE"
  exit 0
fi

if curl --fail --silent --show-error --connect-timeout 2 --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
  printf '0\n' > "$FAIL_FILE"
  echo "WATCHDOG_HEALTHY"
  exit 0
fi

failures=0
if [[ -f "$FAIL_FILE" ]]; then
  failures="$(cat "$FAIL_FILE" 2>/dev/null || printf '0')"
fi
if ! [[ "$failures" =~ ^[0-9]+$ ]]; then
  failures=0
fi
failures=$((failures + 1))
printf '%s\n' "$failures" > "$FAIL_FILE"
echo "WATCHDOG_HEALTH_FAILURES=$failures"

if (( failures < MAX_FAILURES )); then
  exit 0
fi

echo "WATCHDOG_RESTARTING_SERVICE reason=local-health-failed-${MAX_FAILURES}-times"
systemctl restart "$SERVICE_NAME"
printf '0\n' > "$FAIL_FILE"
