# PR #754 — Code completion and production rollout boundary

Status: **Code/CI gate is separate from production rollout evidence.**

## Important clarification

An official Angel One email or support reply is **not required to complete, review, or merge the PR's code**. Do not ask the user to obtain one for PR completion.

The implementation retains conservative software ceilings:
- Maximum 3 physical shared-manager sessions.
- Maximum 1,000 broker tokens per socket.
- No automatic ceiling increase based on assumptions.
- A plan that exceeds either software ceiling is rejected before socket/route state is committed.
- Broker connection-limit responses activate the shared cooldown/admission guard; repeated connection attempts are paced rather than burst-retried.

These controls are software safeguards, not proof of any broker account's entitlement. Do not describe them as broker-confirmed. If the broker rejects a session/subscription in actual operation, keep the feed degraded and use the existing controlled recovery path; do not bypass the cap.

## PR completion gates (code-owned)

- Locked lanes remain:
  - Lane 0: NIFTY spot + NIFTY WEEKLY options.
  - Lane 1: BANKNIFTY spot + BANKNIFTY MONTHLY options.
  - Lane 2: NIFTY + BANKNIFTY index futures only.
- No other index is admitted to those locked lanes.
- Instrument classification uses canonical exchange, segment, token, instrument kind, underlying and expiry family.
- Global session and per-socket token caps reject unsafe plans before live route/socket mutation.
- Recovery recreates only the failed `(mode, shard)` lane; no cross-lane token migration.
- Callback queues are bounded, generation-fenced, observable, and fail closed on overflow/copy-integrity faults.
- Existing consumers retain the synchronous default unless the process-wide shared manager explicitly enables asynchronous dispatch.
- Regression tests cover rollback, stale generations, concurrent recovery, callback shutdown, and route preservation.
- Backend, integration, targeted common-feed and broker-safety workflows pass on the final implementation head.

## Production rollout checks (not PR code-completion blockers)

These checks belong to a separately authorized production activation/soak. They must not be used to demand that the user obtain a broker letter merely to finish this PR.

### Runtime socket-owner inventory

Before production activation, an operator may perform a read-only inventory of services, timers, process trees and manually launched scripts to ensure there is no second independent owner of Angel market-data sockets. Repository review has identified legacy compatibility entry points; do not delete them speculatively without an in-repo caller or a separately approved migration.

Suggested read-only commands:
```bash
systemctl list-units --type=service --all
systemctl list-timers --all
systemctl cat algo-trading.service
ps -eo pid,ppid,user,lstart,args --forest
pgrep -af 'uvicorn|gunicorn|python'
```
Never dump environment variables, process environments, credential files, shell history, access tokens, passwords, TOTP values, or secret-bearing broker URLs.

### Representative production soak

Run only when production activation and an exact market-hours window are separately authorized and a rollback/on-call owner is present. This is not something GitHub CI can claim to have run.

Capture:
- Commit/config, start/end timestamps and market phase.
- Per-lane mode/shard, session generation, expected/actual token counts and subscription acknowledgements/rejections.
- Ingress-to-consumer latency p50/p95/p99 and sample count.
- Process RSS/CPU baseline and peak.
- Callback queue depth/high-water/overflow, callback lag and delivery errors.
- Raw frames, valid quotes, stale/out-of-order/malformed rejects.
- Per-consumer delivery counts, last-delivery age and gaps.
- Ping/pong health, reconnect/failover count and time-to-recovery.
- Retired transport/worker counts and cache size/evictions.

Only exercise pre-approved faults: delayed callback consumer, bounded queue pressure, one-lane disconnect/recovery, delayed close, and reconnect overlap. No broker-wide outage or multi-lane live disruption without a separate risk-approved plan.

NO-GO on silent data loss, false LIVE, cross-lane migration, duplicate socket ownership, unbounded queue/memory growth, unexplained consumer gaps, or failure to recover the failed lane while preserving healthy lanes.

## Closure rule

- **PR code completion:** exact-head code review and CI; no broker letter, VPS inventory, or production soak is required to call the code implementation complete.
- **Production activation:** keep operational checks separate and do not claim a soak or account entitlement that has not been observed.
- No Cloudflare/Vercel work, no VPS changes, no Shoonya readiness/credential work, no strategy formula changes, and no order-execution changes are in scope.
