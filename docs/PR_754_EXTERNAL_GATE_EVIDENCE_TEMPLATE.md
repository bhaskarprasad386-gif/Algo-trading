# PR #754 — External Acceptance Evidence Packet

Status: **OPEN — evidence must be supplied by the account/operator**.

This packet is the controlled close-out record for the three non-CI gates. A green software test cannot substitute for broker account entitlement, host process inventory, or market-hours production measurements. Do not mark a gate passed from assumptions.

## Gate A — Broker WebSocket entitlement

**Owner:** account holder / broker support  
**Required evidence:** written, account-specific confirmation from Angel One (not a generic SDK example).

Record:
- Broker/account reference: redact all but a non-identifying suffix; never commit client ID, API key, access token, TOTP, password, or session cookie.
- Support ticket/email reference and date.
- Maximum simultaneous authenticated market-data WebSocket sessions permitted for this account.
- Maximum subscribed tokens per physical session, including any exchange/segment-specific constraints.
- Whether limits differ for concurrent API sessions, multiple devices, reconnect overlap, or subscription mode.
- Any rate, reconnect, or subscription mutation quotas that apply.
- Explicit confirmation that the proposed 3-session topology is permitted.

**Software safety ceiling remains 3 physical sessions and 1,000 tokens/session.** Broker confirmation must meet or exceed the actual configured demand; do not raise software ceilings to fit an entitlement assumption. If the broker cannot confirm the limits, this gate remains OPEN and production activation is NO-GO.

Evidence attachment/reference:
- Ticket/email reference:
- Confirmed simultaneous sessions:
- Confirmed tokens/session:
- Constraints / expiry date:
- Reviewed by / date:

## Gate B — External/manual socket-owner inventory

**Owner:** VPS/operator  
Repository source inspection found the active application startup path using the shared manager. It also found compatibility entry points that cannot be declared unused solely from repository search:
- legacy `LiveCashFutureOneSecondCollector` two-socket collector;
- `MarketDataEngine` / `LiveMarketDataFeed` standalone default socket factory;
- synthetic standalone injected socket-factory fallback.

Do not delete these compatibility paths until the host/operator inventory proves they are not launched externally.

On the actual host, perform a read-only inventory during a representative running period. Capture:
- systemd unit names and ExecStart commands for the algo service and any market-data sidecars;
- process tree and command lines for Python processes owned by the service account;
- running containers/supervisors and scheduled jobs (cron/systemd timers);
- application logs identifying active socket manager owners and physical session creation;
- operator scripts, notebooks, manual commands, and external imports that may instantiate the legacy classes.

Suggested read-only commands (adapt service/account names to the host; redact secrets before sharing):

```bash
systemctl list-units --type=service --all
systemctl list-timers --all
systemctl cat algo-trading.service
ps -eo pid,ppid,user,lstart,args --forest
pgrep -af 'uvicorn|gunicorn|python'
```

Do not dump environment variables, process environments, credential files, shell history, or full broker URLs containing secrets.

Attestation:
- Host and observation window:
- Services/timers/containers inspected:
- External scripts/notebooks inspected:
- Any legacy collector invocation found (yes/no; details):
- Any standalone engine/feed invocation found (yes/no; details):
- Operator attestation that all active Angel market-data sockets are owned by the shared manager (yes/no):
- Reviewer / date:

If any external caller is found, document its owner and migrate/retire it in a separately tested change before closing this gate. If the operator cannot attest, leave the gate OPEN.

## Gate C — Authorized representative production soak

**Owner:** operator / on-call engineer  
**Preconditions (all mandatory):**
1. Gate A is closed with written account-specific entitlement evidence.
2. Gate B is closed with host/operator attestation.
3. Production activation and the exact soak window are explicitly approved.
4. No deployment or config change is bundled into the soak.
5. Rollback/stop authority and on-call owner are present.

**Do not start a production soak if any precondition is missing.** A CI replay, local synthetic load, off-market test, or skipped VPS workflow is not a production soak.

Capture a market-hours window that includes representative NIFTY weekly, BANKNIFTY monthly, and NIFTY/BANKNIFTY futures traffic, without adding other indices to the locked lanes.

Required raw measurements:
- Start/end timestamps, market session phase, duration, commit SHA and config version.
- Per lane: mode/shard, expected and actual token count, session generation, connect/auth state, subscription acknowledgement/rejection counts.
- Ingress-to-consumer latency p50/p95/p99, with measurement method and sample count.
- Process RSS baseline/peak/end; CPU baseline/peak.
- Callback queue capacity, high-water, overflow/rejection count, callback processing lag.
- Raw frame and valid quote counts; stale/out-of-order/malformed rejection counts.
- Per-consumer delivery counts, errors, last-delivery age and any observed delivery gap.
- Ping/pong counts and latency/age where observable.
- Reconnect/failover count, trigger, time-to-recovery, and healthy-lane continuity.
- Cache size/evictions/TTL expirations and manager worker/retired-worker counts.
- Logs/metrics artifact references, with secrets and raw broker payloads removed.

Exercise only pre-approved controlled faults in an authorized window: delayed callback consumer, bounded queue pressure, one-lane disconnect/recovery, delayed close, and reconnect overlap. Do not deliberately inject broker-wide outages, credential failures, or multi-lane disruption in a live market without a separate risk-approved plan.

**Go/no-go:** thresholds must be agreed before the run from the actual service SLO and measured baseline; do not invent numeric thresholds after observing results. NO-GO on any silent data loss, false LIVE, cross-lane token migration, duplicate active ownership, session/token entitlement breach, unbounded queue/memory growth, unexplained consumer gap, or inability to recover the failed lane without disturbing healthy lanes.

Evidence:
- Approved change/soak reference:
- Start/end and market window:
- Commit/config:
- Raw metrics/log artifact references:
- Per-lane token/session evidence:
- Latency and memory summary:
- Queue/overflow summary:
- Faults exercised and recovery durations:
- Deviations/incidents:
- Go/no-go decision and approver:

## Lane contract to verify during inventory and soak

- Socket 1 / lane 0: NIFTY spot + NIFTY WEEKLY CE/PE only.
- Socket 2 / lane 1: BANKNIFTY spot + BANKNIFTY MONTHLY CE/PE only.
- Socket 3 / lane 2: NIFTY + BANKNIFTY index futures only.
- No other index on these locked lanes; no cross-lane migration during failure recovery.

## Closure rule

Each gate is closed only when its evidence fields are completed and reviewed. Keep PR #754 draft and production activation disabled until all required evidence is attached and the final exact-head CI is green. This packet is a process/evidence aid; it is not itself proof that any external gate passed.
