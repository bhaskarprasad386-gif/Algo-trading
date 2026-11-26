#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/Algo-Trading"
LIVE_DIR="$APP_DIR/frontend"
SERVICE="algo-frontend.service"
TARGET_SHA="${1:?usage: deploy-frontend-vps.sh <commit-sha>}"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid target SHA" >&2; exit 2; }
[[ -d "$APP_DIR/.git" && -d "$LIVE_DIR" ]] || { echo "VPS app/frontend checkout missing" >&2; exit 1; }
[[ "$(git -C "$APP_DIR" rev-parse HEAD)" == "$TARGET_SHA" ]] || { echo "Checkout SHA does not match requested deploy SHA" >&2; exit 1; }
[[ "$(systemctl show "$SERVICE" -p LoadState --value)" == "loaded" ]] || { echo "$SERVICE is not installed; refusing to invent a service" >&2; exit 1; }
WORKING_DIR="$(systemctl show "$SERVICE" -p WorkingDirectory --value)"
[[ "$WORKING_DIR" == "$LIVE_DIR" ]] || { echo "Unexpected frontend WorkingDirectory: $WORKING_DIR" >&2; exit 1; }
command -v node >/dev/null && command -v npm >/dev/null
command -v curl >/dev/null && command -v flock >/dev/null

exec 9>/run/lock/algo-frontend-deploy.lock
flock -n 9 || { echo "Another frontend deployment is running" >&2; exit 1; }

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RELEASE_DIR="$APP_DIR/.frontend-build-$TARGET_SHA"
BACKUP_DIR="$APP_DIR/.frontend-rollback-$STAMP"
NEW_DIST="$LIVE_DIR/.next.new-$TARGET_SHA"
NEW_MODULES="$LIVE_DIR/node_modules.new-$TARGET_SHA"
OLD_DIST="$LIVE_DIR/.next"
OLD_MODULES="$LIVE_DIR/node_modules"
DIST_BACKED_UP=0
MODULES_BACKED_UP=0
SWAPPED=0

cleanup() {
  local status=$?
  if [[ "$status" -ne 0 && "$SWAPPED" -eq 1 ]]; then
    echo "Frontend health gate failed; rolling back previous frontend runtime"
    systemctl stop "$SERVICE" || true
    rm -rf "$LIVE_DIR/.next" "$LIVE_DIR/node_modules"
    if [[ "$DIST_BACKED_UP" -eq 1 && -d "$BACKUP_DIR/next" ]]; then mv "$BACKUP_DIR/next" "$OLD_DIST"; fi
    if [[ "$MODULES_BACKED_UP" -eq 1 && -d "$BACKUP_DIR/node_modules" ]]; then mv "$BACKUP_DIR/node_modules" "$OLD_MODULES"; fi
    systemctl start "$SERVICE" || true
  fi
  rm -rf "$NEW_DIST" "$NEW_MODULES" "$RELEASE_DIR"
  if [[ "$status" -eq 0 ]]; then
    # Keep the previous build and dependency tree as a manual rollback point.
    find "$APP_DIR" -maxdepth 1 -type d -name '.frontend-rollback-*' -printf '%T@ %p\n' | sort -nr | tail -n +4 | cut -d' ' -f2- | while IFS= read -r old; do rm -rf "$old"; done
  fi
  exit "$status"
}
trap cleanup EXIT

echo "FRONTEND_DEPLOY_TARGET=$TARGET_SHA"
echo "FRONTEND_SERVICE_WORKDIR=$WORKING_DIR"
mkdir -p "$BACKUP_DIR"
git -C "$APP_DIR" worktree add --detach "$RELEASE_DIR" "$TARGET_SHA"

# Reuse only runtime frontend environment files; never print their contents.
for env_name in .env .env.local .env.production .env.production.local; do
  if [[ -f "$LIVE_DIR/$env_name" ]]; then cp -p "$LIVE_DIR/$env_name" "$RELEASE_DIR/frontend/$env_name"; fi
done

cd "$RELEASE_DIR/frontend"
npm install --no-audit --no-fund
npm run build

[[ -d "$RELEASE_DIR/frontend/.next" ]] || { echo "Next.js build output missing" >&2; exit 1; }
[[ -d "$RELEASE_DIR/frontend/node_modules" ]] || { echo "Frontend node_modules missing after install" >&2; exit 1; }

# Prepare same-filesystem replacements before stopping the currently serving frontend.
rm -rf "$NEW_DIST" "$NEW_MODULES"
cp -a "$RELEASE_DIR/frontend/.next" "$NEW_DIST"
cp -a "$RELEASE_DIR/frontend/node_modules" "$NEW_MODULES"

systemctl stop "$SERVICE"
if [[ -d "$OLD_DIST" ]]; then mv "$OLD_DIST" "$BACKUP_DIR/next"; DIST_BACKED_UP=1; fi
if [[ -d "$OLD_MODULES" ]]; then mv "$OLD_MODULES" "$BACKUP_DIR/node_modules"; MODULES_BACKED_UP=1; fi
mv "$NEW_DIST" "$OLD_DIST"
mv "$NEW_MODULES" "$OLD_MODULES"
SWAPPED=1
systemctl start "$SERVICE"

healthy=0
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --connect-timeout 2 --max-time 5 http://127.0.0.1:3000/ >/dev/null \
    && curl --fail --silent --show-error --connect-timeout 2 --max-time 5 http://127.0.0.1:3000/errors >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done
if [[ "$healthy" -ne 1 ]]; then
  systemctl --no-pager --full status "$SERVICE" || true
  journalctl -u "$SERVICE" -n 80 --no-pager || true
  echo "Frontend HTTP health gate failed" >&2
  exit 1
fi

echo "FRONTEND_DEPLOY_STATUS=SUCCEEDED"
echo "FRONTEND_DEPLOY_SHA=$TARGET_SHA"
echo "FRONTEND_ROOT_HTTP=200"
echo "FRONTEND_ERRORS_HTTP=200"
systemctl show "$SERVICE" -p ActiveState -p SubState -p MainPID -p NRestarts
