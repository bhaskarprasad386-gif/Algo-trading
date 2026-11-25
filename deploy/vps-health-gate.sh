#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 3 || $# -gt 5 ]]; then
  echo "usage: vps-health-gate.sh URL LABEL ATTEMPTS [MAX_TIME_SECONDS] [RETRY_DELAY_SECONDS]" >&2
  exit 2
fi
url="$1"
label="$2"
attempts="$3"
max_time="${4:-8}"
retry_delay="${5:-2}"
if [[ ! "$attempts" =~ ^[1-9][0-9]*$ || ! "$max_time" =~ ^[1-9][0-9]*$ || ! "$retry_delay" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "invalid retry/timeout parameters" >&2
  exit 2
fi
curl_bin="${HEALTH_GATE_CURL_BIN:-curl}"
sleep_bin="${HEALTH_GATE_SLEEP_BIN:-sleep}"
for ((attempt = 1; attempt <= attempts; attempt++)); do
  if "$curl_bin" --fail --silent --show-error --connect-timeout 2 --max-time "$max_time" "$url"; then
    printf '%s=PASS\n' "$label"
    exit 0
  fi
  printf '%s_RETRY_ATTEMPT=%s\n' "$label" "$attempt" >&2
  if ((attempt < attempts)); then "$sleep_bin" "$retry_delay"; fi
done
printf '%s=FAIL after %s attempts\n' "$label" "$attempts" >&2
exit 1
