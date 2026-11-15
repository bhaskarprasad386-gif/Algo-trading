# Batch 0 — VPS, Frontend Hosting, and Deployment Recovery Audit

**Status:** Workflow/source audit only. No VPS command, deployment, restart, DNS change, or workflow dispatch was performed.  
**Audited base:** `main` at `8da655f0c287251cf1602cae484714a7c26fcf15`.

## 1. Current hosting/deployment shape

- Frontend is a Next.js application under `frontend/` (Next 15.5.29, React 19.1.2). Its package scripts provide dev/build/start/typecheck.
- The inspected `.github/workflows/vps-deploy.yml` deploys the repository to `/opt/Algo-Trading`, updates the backend Git checkout, and invokes `deploy/bootstrap-vps.sh` when runtime files changed and deployment is not deferred.
- The VPS deploy workflow restarts `algo-trading.service` through the bootstrap script; it does not define a separately supervised Next.js frontend service, frontend build/install/start, or frontend health check.
- Therefore the inspected VPS workflow is not a verified full-stack frontend + API deployment path. A frontend process manager/systemd unit, reverse-proxy routing, TLS, and health/rollback path still need to be implemented and verified before cutover.
- Android Gradle and release workflow defaults still target the Render backend; this must be migrated only after the new endpoint is stable and compatible.

## 2. Existing deployment safeguards and limitations

Positive controls:
- GitHub Actions concurrency group `vps-service-mutation` serializes VPS deploy workflow runs.
- SSH host-key checking is strict after keyscan; public-key authentication is preflighted.
- Remote script refuses to replace a non-Git app directory and uses `git merge --ff-only`.
- Market-hours restart deferral and runtime-change detection exist.
- Pre/post health probes, service status, runtime diagnostics, and a Box spool path check are present in the deploy script.

Limitations:
- Deployment work runs synchronously inside one SSH session. The prior run reached SSH auth preflight but later timed out without a visible `REMOTE_DEPLOY_SCRIPT_STARTED` marker. The remote outcome therefore remains unknown from that run's logs.
- The marker is written only when the remote script starts; absence of the marker is useful evidence but not a definitive proof about remote state if logging itself was interrupted.
- There is no durable remote deployment job record with a lock, immutable commit, status, timestamps, and log path that can be queried after SSH disconnect.
- GitHub concurrency serializes workflow runs but is not a remote host lock protecting against manual SSH deployments or another automation system.
- The script does substantial synchronous diagnostics (disk usage, largest files, journals, API health) in the same SSH session before/after deployment, increasing the amount of time that transport remains open.
- The workflow's service health checks cover FastAPI endpoints. They do not prove that the Next.js frontend, reverse proxy, browser WebSocket path, Cloudflare edge, or Android app are working end-to-end.

## 3. Read-only diagnostics already available

- `.github/workflows/vps-read-only-diagnostic.yml` is manual-only and runs read-only DNS, service state, local health, market-data health, scanner snapshots, and recent journal checks.
- `.github/workflows/vps-health-probe.yml` runs scheduled/manual checks and reports app directory, current Git commit/worktree state, API runtime health, scanner snapshots, and Box Spread diagnostics.
- Neither workflow was dispatched during this audit. No claim is made about the current VPS service state, current deployed commit, memory/disk state, or API health.

Before retrying any deployment, run a read-only check that explicitly records:
1. host identity and current time;
2. `systemctl show algo-trading.service` (active/substate, PID, restart count, memory);
3. `git -C /opt/Algo-Trading rev-parse HEAD` and worktree status;
4. the exact run-specific `/tmp/algo-trading-deploy-<run_id>.started` marker for the timed-out run;
5. recent service journal and deployment log;
6. disk/inode/memory pressure;
7. local FastAPI health, live-data health, common-feed health, and Box Spread diagnostics;
8. no service restart, package install, git pull, or configuration mutation.

## 4. Recommended durable deployment runner

Implement this as a separate reviewed deployment change, not as an incidental scanner/engine edit:

1. GitHub workflow connects over SSH and submits a unique deployment job ID plus target immutable commit SHA.
2. Remote wrapper obtains a host-level lock (e.g. `flock`) and rejects duplicate/in-progress jobs.
3. Wrapper writes a durable job record and log file before detaching work; response returns the job ID immediately.
4. Remote worker performs preflight, disk check, backup/restore-point check, immutable checkout, additive migration, backend/frontend build, supervised restart, and health checks.
5. Worker atomically updates job status: `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, or `UNKNOWN_REQUIRES_INSPECTION`.
6. A separate read-only status action queries the existing job ID and returns the log tail. It must never create a second job.
7. Failed health checks restore the previous application artifact/configuration and leave schema backward-compatible. A database restore is a separate approved recovery operation, not an automatic deploy rollback.
8. Keep Vercel available as the rollback frontend until VPS + Cloudflare staging and production soak are explicitly approved.

## 5. Target full-stack topology

- Cloudflare DNS/proxy/WAF/TLS at the edge.
- Reverse proxy on VPS routes frontend HTTP, backend REST, and authenticated WebSocket paths independently.
- Next.js frontend runs as a dedicated supervised process/container, separate from FastAPI.
- FastAPI retains its current single-worker assumption until page-push/event fanout has a shared transport and sequence/resync semantics.
- PostgreSQL and any event transport remain private; no public DB/Redis or broker-secret endpoint.
- Independent frontend, API, WebSocket, DB-readiness, and scanner-feed health signals.
- Immutable release IDs and rollback to the previous frontend/backend artifacts.

## 6. Cutover gates

- [ ] Current VPS state reconciled; prior timed-out deployment is understood.
- [ ] Durable remote job/lock/status mechanism is implemented and tested.
- [ ] Frontend systemd/container service and reverse-proxy config exist.
- [ ] Cloudflare DNS/TLS/WAF and WebSocket upgrade behavior verified on staging hostname.
- [ ] Frontend/API/auth/CORS/mobile end-to-end tests pass.
- [ ] Scanner freshness and existing shared WebSocket socket/subscription counts remain unchanged.
- [ ] Backup restore and application rollback rehearsals pass.
- [ ] Vercel remains available through an agreed production soak period.
- [ ] Explicit owner approval before production DNS cutover or Vercel decommission.

## 7. Safety decision

No VPS state was queried in this audit and no remote workflow was run. No deployment, service restart, frontend hosting change, Cloudflare/DNS change, or Vercel removal occurred. Do not retry the previously timed-out deployment blindly. Keep Vercel and existing Android Render URL unchanged until a complete replacement path is verified.
