#!/usr/bin/env bash
set -euo pipefail

: "${VPS_HOST:?VPS_HOST must be set}"

VPS_SSH_PORT="${VPS_SSH_PORT:-22}"
EVIDENCE_FILE="${VPS_HEALTH_EVIDENCE_FILE:-/tmp/vps-health-probe.txt}"
KNOWN_HOSTS_FILE="${VPS_KNOWN_HOSTS_FILE:-$HOME/.ssh/known_hosts}"
KEYSCAN_BIN="${VPS_KEYSCAN_BIN:-ssh-keyscan}"
TIMEOUT_BIN="${VPS_TIMEOUT_BIN:-timeout}"
SLEEP_BIN="${VPS_SLEEP_BIN:-sleep}"
PYTHON_BIN="${VPS_PYTHON_BIN:-python3}"
MAX_ATTEMPTS="${VPS_KEYSCAN_ATTEMPTS:-3}"
COMMAND_TIMEOUT="${VPS_KEYSCAN_COMMAND_TIMEOUT_SECONDS:-8}"
CONNECT_TIMEOUT="${VPS_KEYSCAN_CONNECT_TIMEOUT_SECONDS:-5}"
RETRY_DELAY="${VPS_KEYSCAN_RETRY_DELAY_SECONDS:-2}"
TCP_TIMEOUT="${VPS_TCP_PROBE_TIMEOUT_SECONDS:-3}"

mkdir -p "$(dirname "$EVIDENCE_FILE")" "$(dirname "$KNOWN_HOSTS_FILE")"
: > "$EVIDENCE_FILE"
exec > >(tee -a "$EVIDENCE_FILE") 2>&1

printf 'VPS_SSH_KEYSCAN_DIAGNOSTICS_VERSION=1\n'
printf 'VPS_SSH_KEYSCAN_PORT=%s\n' "$VPS_SSH_PORT"
printf 'VPS_SSH_KEYSCAN_ATTEMPTS_LIMIT=%s\n' "$MAX_ATTEMPTS"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

keyscan_ok=0
last_failure_kind=UNKNOWN

for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt++)); do
  stdout_file="$tmpdir/keyscan-${attempt}.stdout"
  stderr_file="$tmpdir/keyscan-${attempt}.stderr"

  if "$TIMEOUT_BIN" "$COMMAND_TIMEOUT" "$KEYSCAN_BIN" \
      -T "$CONNECT_TIMEOUT" -p "$VPS_SSH_PORT" -H "$VPS_HOST" \
      >"$stdout_file" 2>"$stderr_file"; then
    exit_code=0
  else
    exit_code=$?
  fi

  stdout_bytes="$(wc -c < "$stdout_file" | tr -d '[:space:]')"
  stderr_bytes="$(wc -c < "$stderr_file" | tr -d '[:space:]')"
  printf 'SSH_KEYSCAN_ATTEMPT=%s EXIT_CODE=%s STDOUT_BYTES=%s STDERR_BYTES=%s\n' \
    "$attempt" "$exit_code" "$stdout_bytes" "$stderr_bytes"

  if [[ -s "$stderr_file" ]]; then
    printf 'SSH_KEYSCAN_STDERR='
    sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}/[redacted-ip]/g' "$stderr_file" |
      tr '\n' ' ' | cut -c1-500
    printf '\n'
  fi

  if [[ "$exit_code" -eq 0 && -s "$stdout_file" ]]; then
    cat "$stdout_file" >> "$KNOWN_HOSTS_FILE"
    chmod 600 "$KNOWN_HOSTS_FILE"
    printf 'VPS_SSH_KEYSCAN=OK attempt=%s\n' "$attempt"
    keyscan_ok=1
    break
  fi

  if [[ "$exit_code" -eq 124 || "$exit_code" -eq 137 ]]; then
    last_failure_kind=TIMEOUT
  elif [[ "$exit_code" -eq 0 ]]; then
    last_failure_kind=EMPTY_OUTPUT
  else
    last_failure_kind=NONZERO_EXIT_NO_HOST_KEY
  fi
  printf 'SSH_KEYSCAN_ATTEMPT_FAILURE_KIND=%s\n' "$last_failure_kind"

  if [[ "$attempt" -lt "$MAX_ATTEMPTS" ]]; then
    "$SLEEP_BIN" "$RETRY_DELAY"
  fi
done

if [[ "$keyscan_ok" -ne 1 ]]; then
  printf 'VPS_SSH_KEYSCAN=FAILED attempts=%s last_failure=%s\n' \
    "$MAX_ATTEMPTS" "$last_failure_kind"
  if "$PYTHON_BIN" - "$VPS_HOST" "$VPS_SSH_PORT" "$TCP_TIMEOUT" <<'PY'
import errno
import socket
import sys

host, port, timeout = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
try:
    with socket.create_connection((host, port), timeout=timeout):
        print("VPS_SSH_TCP_CONNECT=OK")
except socket.gaierror as exc:
    print(f"VPS_SSH_TCP_CONNECT=DNS_FAILED errno={exc.errno}")
except OSError as exc:
    if exc.errno == errno.ECONNREFUSED:
        result = "CONNECTION_REFUSED"
    elif exc.errno == errno.ETIMEDOUT:
        result = "TIMEOUT"
    else:
        result = "FAILED"
    print(f"VPS_SSH_TCP_CONNECT={result} errno={exc.errno} error={type(exc).__name__}")
PY
  then
    :
  else
    printf 'VPS_SSH_TCP_DIAGNOSTIC=UNAVAILABLE\n'
  fi
  exit 1
fi
