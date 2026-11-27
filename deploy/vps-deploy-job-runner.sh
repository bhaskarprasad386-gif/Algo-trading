#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/var/lib/algo-trading-deploy"
JOBS_DIR="$ROOT_DIR/jobs"
LOCK_FILE="/var/lock/algo-trading-deploy.lock"
APP_DIR="/opt/Algo-Trading"
SERVICE_NAME="algo-trading.service"
JOB_ID="${2:-}"
TARGET_SHA="${3:-}"
FORCE_RESTART="${4:-false}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
valid_job_id() { [[ "$1" =~ ^[0-9]{1,24}$ ]]; }
valid_sha() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }
job_dir() { printf '%s/%s' "$JOBS_DIR" "$1"; }
unit_name() { printf 'algo-trading-deploy-%s' "$1"; }

write_state() {
  local status="$1" exit_code="${2:-null}" dir tmp now
  dir="$(job_dir "$JOB_ID")"
  mkdir -p "$dir"
  tmp="$dir/status.json.tmp"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"job_id":"%s","status":"%s","target_sha":"%s","updated_at":"%s","exit_code":%s}\n' \
    "$JOB_ID" "$status" "$TARGET_SHA" "$now" "$exit_code" > "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$dir/status.json"
}

submit_job() {
  [[ "$(id -u)" -eq 0 ]] || die "remote deploy job submission requires root"
  valid_job_id "$JOB_ID" || die "job ID must be numeric"
  valid_sha "$TARGET_SHA" || die "target SHA must be a full 40-character commit"
  [[ "$FORCE_RESTART" == "true" || "$FORCE_RESTART" == "false" ]] || die "force_restart must be true or false"
  command -v systemd-run >/dev/null || die "systemd-run is unavailable"
  command -v flock >/dev/null || die "flock is unavailable"
  mkdir -p "$JOBS_DIR"
  chmod 700 "$ROOT_DIR" "$JOBS_DIR"
  local dir unit
  dir="$(job_dir "$JOB_ID")"
  unit="$(unit_name "$JOB_ID")"
  if [[ -e "$dir/status.json" ]]; then
    cat "$dir/status.json"
    printf 'DUPLICATE_SUBMISSION_BLOCKED=true\n'
    exit 0
  fi
  if [[ -e "$dir" ]]; then
    die "job directory exists without a durable status record; inspect manually, do not resubmit"
  fi
  mkdir -m 700 "$dir"
  install -m 700 "$0" "$dir/runner.sh"
  : > "$dir/job.log"
  chmod 600 "$dir/job.log"
  write_state "QUEUED"
  if ! systemd-run --no-block \
      --unit="$unit" \
      --property=Type=oneshot \
      --property=RuntimeMaxSec=2700s \
      /bin/bash "$dir/runner.sh" run "$JOB_ID" "$TARGET_SHA" "$FORCE_RESTART"; then
    write_state "FAILED" 1
    die "systemd could not start the durable remote deployment worker"
  fi
  printf 'REMOTE_JOB_SUBMITTED=true\n'
  cat "$dir/status.json"
}

run_job() {
  [[ "$(id -u)" -eq 0 ]] || die "deployment worker requires root"
  valid_job_id "$JOB_ID" || die "job ID must be numeric"
  valid_sha "$TARGET_SHA" || die "target SHA must be a full 40-character commit"
  [[ "$FORCE_RESTART" == "true" || "$FORCE_RESTART" == "false" ]] || die "force_restart must be true or false"
  local dir unit final_status="SUCCEEDED" exit_status=0
  dir="$(job_dir "$JOB_ID")"
  unit="$(unit_name "$JOB_ID")"
  [[ -f "$dir/status.json" && -f "$dir/job.log" ]] || die "durable job record is missing"
  exec >> "$dir/job.log" 2>&1
  finish() {
    local rc=$?
    if [[ "$final_status" == "DEFERRED_MARKET_HOURS" ]]; then
      write_state "$final_status" 0
      return
    fi
    if [[ "$rc" -eq 0 ]]; then
      write_state "SUCCEEDED" 0
    else
      write_state "FAILED" "$rc"
    fi
  }
  trap finish EXIT
  write_state "RUNNING"
  printf 'JOB_ID=%s\nTARGET_SHA=%s\nSTARTED_AT=%s\n' "$JOB_ID" "$TARGET_SHA" "$(date -Is)"

  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    printf 'REMOTE_DEPLOY_LOCK_BUSY=true\n'
    exit_status=75
    exit "$exit_status"
  fi

  if [[ "$FORCE_RESTART" != "true" ]]; then
    local hhmm dow hhmm_num
    hhmm="$(TZ=Asia/Kolkata date +%H%M)"
    hhmm_num=$((10#$hhmm))
    dow="$(TZ=Asia/Kolkata date +%u)"
    if [[ "$dow" -le 5 && "$hhmm_num" -ge 915 && "$hhmm_num" -lt 1530 ]]; then
      printf 'DEPLOY_DEFERRED_MARKET_HOURS=IST_%s\n' "$hhmm"
      final_status="DEFERRED_MARKET_HOURS"
      exit 0
    fi
  fi

  [[ -d "$APP_DIR/.git" ]] || die "application checkout is missing"
  [[ -f "$APP_DIR/deploy/bootstrap-vps.sh" ]] || die "bootstrap script is missing"
  cd "$APP_DIR"
  git diff --quiet || die "working tree has uncommitted changes; refusing deployment"
  git diff --cached --quiet || die "index has staged changes; refusing deployment"
  printf 'PRE_DEPLOY_COMMIT=%s\n' "$(git rev-parse HEAD)"
  printf 'PRE_DEPLOY_DISK=\n'
  df -h / /opt
  printf 'PRE_DEPLOY_MEMORY=\n'
  free -h
  git fetch origin main
  git cat-file -e "$TARGET_SHA^{commit}" || die "target commit is not present after fetching origin/main"
  git merge-base --is-ancestor HEAD "$TARGET_SHA" || die "target commit is not a fast-forward descendant of the current VPS checkout"
  git merge --ff-only "$TARGET_SHA"
  [[ "$(git rev-parse HEAD)" == "$TARGET_SHA" ]] || die "checkout does not match immutable target SHA"
  export DEPLOY_TARGET_SHA="$TARGET_SHA"
  bash "$APP_DIR/deploy/bootstrap-vps.sh"
  [[ "$(git rev-parse HEAD)" == "$TARGET_SHA" ]] || die "bootstrap changed the immutable target commit"
  systemctl is-active --quiet "$SERVICE_NAME" || die "service is not active after bootstrap"
  curl --fail --silent --show-error --connect-timeout 2 --max-time 10 http://127.0.0.1:8000/health
  printf '\nDEPLOY_SUCCEEDED_COMMIT=%s\n' "$(git rev-parse HEAD)"
  printf 'SERVICE=%s\n' "$(systemctl is-active "$SERVICE_NAME")"
  printf 'FINISHED_AT=%s\n' "$(date -Is)"
}

show_status() {
  valid_job_id "$JOB_ID" || die "job ID must be numeric"
  local dir unit unit_state job_status
  dir="$(job_dir "$JOB_ID")"
  unit="$(unit_name "$JOB_ID")"
  [[ -f "$dir/status.json" ]] || die "job record not found"
  cat "$dir/status.json"
  unit_state="$(systemctl is-active "$unit" 2>/dev/null || true)"
  printf 'SYSTEMD_UNIT_STATE=%s\\n' "$unit_state"
  job_status="$(grep -oE '"status":"[A-Z_]+"' "$dir/status.json" | cut -d '"' -f 4)"
  if [[ "$job_status" == "RUNNING" && "$unit_state" != "active" && "$unit_state" != "activating" ]]; then
    printf 'REMOTE_JOB_STATE=UNKNOWN_REQUIRES_INSPECTION\\n'
  fi
}

show_logs() {
  valid_job_id "$JOB_ID" || die "job ID must be numeric"
  local dir
  dir="$(job_dir "$JOB_ID")"
  [[ -f "$dir/job.log" ]] || die "job log not found"
  tail -n 120 "$dir/job.log"
}

case "${1:-}" in
  submit) submit_job ;;
  run) run_job ;;
  status) show_status ;;
  logs) show_logs ;;
  *) die "usage: $0 {submit|run|status|logs} JOB_ID TARGET_SHA [force_restart]" ;;
esac
