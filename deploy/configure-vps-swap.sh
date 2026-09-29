#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_SWAP_BYTES=$((8 * 1024 * 1024 * 1024))
SWAPFILE="/swapfile-algo-supplemental"

log() { printf '\n[algo-memory] %s\n' "$*"; }
die() { printf '\n[algo-memory] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run as root."

current_bytes="$(awk '/SwapTotal:/ {print $2 * 1024}' /proc/meminfo)"
current_bytes="${current_bytes:-0}"

if (( current_bytes >= TARGET_SWAP_BYTES )); then
  log "Swap already at or above 8 GiB; no change required."
  swapon --show --bytes || true
  exit 0
fi

if [[ -e "$SWAPFILE" ]]; then
  file_bytes="$(stat -c '%s' "$SWAPFILE")"
  if (( file_bytes != TARGET_SWAP_BYTES )); then
    if swapon --show=NAME --noheadings | grep -Fxq "$SWAPFILE"; then
      swapoff "$SWAPFILE"
    fi
    rm -f "$SWAPFILE"
  fi
fi

if [[ ! -e "$SWAPFILE" ]]; then
  log "Creating 8 GiB swap file."
  fallocate -l 8G "$SWAPFILE"
  chmod 600 "$SWAPFILE"
  mkswap "$SWAPFILE" >/dev/null
fi

if ! swapon --show=NAME --noheadings | grep -Fxq "$SWAPFILE"; then
  swapon "$SWAPFILE"
fi

if ! grep -Eq '^[[:space:]]*/swapfile-algo-supplemental[[:space:]]+none[[:space:]]+swap[[:space:]]' /etc/fstab; then
  printf '%s\n' "$SWAPFILE none swap sw 0 0" >> /etc/fstab
fi

actual_bytes="$(awk '/SwapTotal:/ {print $2 * 1024}' /proc/meminfo)"
(( actual_bytes >= TARGET_SWAP_BYTES )) || die "Swap target verification failed."

log "Swap configuration verified."
swapon --show --bytes
free -h
