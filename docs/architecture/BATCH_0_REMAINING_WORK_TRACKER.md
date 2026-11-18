# Batch 0 — Consolidated Remaining-Work Tracker

**Updated:** 2026-11-18  
**Execution branch:** `batch0/emergency-recovery-coordinator`  
**Current PR:** #633 — fail-closed multi-leg emergency recovery  
**Release posture:** Paper-first; all Live broker order mutations remain disabled.

This tracker consolidates the outstanding Batch 0 findings. A green CI run or a merged PR is not, by itself, proof of production readiness. Runtime-only evidence and broker certification are tracked separately.

## Non-negotiable invariants

1. Box Spread, Credit Butterfly and Condor are application-level baskets. All legs must be approved together against one coherent fresh snapshot and the declared ratios.
2. A basket is successful only after every required leg is broker-confirmed filled to its exact target quantity and the durable ledger is reconciled.
3. A partial/rejected/unknown leg freezes the intent, stops unsent legs, cancels working entry orders, reconciles late fills, and starts bounded recovery for confirmed residual exposure.
4. A cancel acknowledgement is not proof of cancellation; an IOC acknowledgement is not proof of a fill; no residual is declared flat without authoritative reconciled broker positions.
5. Separate broker orders cannot promise exchange-level simultaneous fills. Never describe application-level coordination as exchange atomicity.
6. No broker adapter may be enabled for Live until its capability, account identity, durable session, status normalization, order mutation, reconciliation, risk and recovery gates pass.
7. Preserve existing Box/IV/IV Skew Flip scanners, shared market-data WebSocket, subscriptions, page-push contracts, and unrelated runners.
8. No backtesting, Cash Future, Synthetic Future or Calendar Spread implementation is part of this execution batch.

## Workstream status

| Workstream | Current evidence / implementation | Remaining closure gate |
|---|---|---|
| Durable broker fill lifecycle | PRs #628–#631 add account-scoped identity, replay, cumulative partial-fill and overfill protections. Latest known Backend CI for #629–#631 passed. | Recheck exact-head CI after final stack changes; merge in dependency order only after all required checks pass. |
| Durable disarm / account identity | PRs #626 and #627 are open; #627 latest known Backend + Integration CI passed. | Recheck current exact-head checks and merge after review. |
| Multi-leg recovery for Box / Butterfly / Condor | PR #633 adds a fail-closed recovery coordinator, test fakes and protocol boundaries. Fast Broker Safety Gate passed on an earlier exact-head attempt; full Backend Tests were still running at last inspection. | Exact final-head Backend + Fast Gate + Integration CI; then adapter and durable-journal implementations. |
| Recovery broker/journal integration | Explicit protocols require authoritative account-scoped order/position snapshots, cancellation, fresh quotes, LIMIT/IOC close, intent freeze, exclusive instrument lease, durable attempt IDs, kill switch, audit, alerts and risk reservation. | Implement and certify broker-specific adapters and durable journal. Do not wire a fake/test adapter into live routes. |
| Paper entry receipt replay | Source audit records successful four-leg receipt replay and corruption rejection tests on the Paper route. | Confirm exact current main source/CI; add deterministic process-crash and PostgreSQL multi-worker tests around each commit boundary. |
| Paper simulator durability | Simulator state is still process-local; incomplete claimed entries are intentionally blocked for manual review. | Persist normalized Paper attempt/result events or use a transaction/outbox design; keep no-blind-replay semantics. |
| Paper authorization | Paper ledger uses legacy account keys and an owner scope; it is not a broker-account identity. | Owner-bound authorization on every read/write and a safe legacy mapping; never convert legacy Paper rows into Live accounts. |
| Broker capabilities | Five read-only history providers exist (Angel One, Zerodha, FYERS, Shoonya, m.Stock); only Angel One is in the trading registry and all mutation methods remain fail-closed. | Capability-aware composition, durable sessions, open-order evidence, positions/funds, modify/cancel races, client IDs, rate limits, redaction and broker-approved non-production certification. |
| Risk and basket preflight | Existing primitives cover parts of risk/slippage/kill-switch checks; PR #633 requires quote freshness, depth, lot/tick, position baseline and exclusive lease at recovery. | Shared production preflight must validate all legs/ratios, net edge after fees, margin, exposure, daily loss, price collars, throttles and coherent snapshot before any order submit. |
| PostgreSQL / migration | Startup advisory lock and selected concurrency/failure tests were merged previously. | Versioned migration ledger, dedicated migration stage, backup/restore rehearsal, and real PostgreSQL concurrency tests for account claims, fills, recovery attempts and concurrent exits. |
| Runtime / WebSocket baseline | Source-derived runbook exists. PR #633 expands manual read-only diagnostic workflow to collect service/Git/resource state, three runtime-health samples, API health, diagnostics summary and one five-minute dashboard WS cadence sample; artifact retention is three days. | Manually dispatch workflow; capture market-hours and off-market samples; inspect artifact; compare scanner/API/socket metrics. This chat cannot claim those VPS observations until workflow has actually run. |
| VPS deployment recovery | Prior SSH timeout left deployment outcome unknown. Workflow/source audit identifies missing durable remote job/lock/status. | Read-only reconcile current VPS first. Implement durable remote job ID/lock/log/status runner and test it without restarting production. |
| Frontend / Cloudflare hosting | Existing VPS workflow deploys backend; full Next.js frontend service/reverse proxy/TLS/WS path is not verified. | Build staging frontend/API topology, health/rollback, Cloudflare/TLS/CORS/WS/mobile E2E and soak. Keep Vercel as rollback. No DNS cutover here. |
| Android session security | Native owner token is process-memory only; current defaults target Render; no remote signed-page renderer or app push client. | Keystore-backed session lifecycle, backup exclusions, logout/revocation, authenticated push, signed versioned manifest, component/capability allowlist, ETag/cache/rollback and old-app compatibility tests. |
| Multi-worker event transport | Page-push state is process-local; single-worker assumption remains. | Durable event/outbox plus shared transport and sequence/resync/account isolation tests before scaling API workers. |
| Scanner non-regression | Scanner and subscription contracts are explicitly protected; no scanner code changed in this PR. | Compare API payloads, feed socket/subscription counts, page channels and latency against actual runtime baseline. |

## Current PR dependency chain

- #626 — disarm persistence tests (independent main-based PR).
- #627 — account-scoped broker identity guard (main-based).
- #628 — broker fill/reconciliation lifecycle (stacked on #627).
- #629 — fill replay/four-leg completion (stacked on #628).
- #630 — cumulative partial fills (stacked on #629).
- #631 — cumulative overfill rejection (stacked on #630).
- #633 — emergency recovery coordinator (stacked on #631).

Do not merge stacked PRs out of order. Rebase/update each dependent branch only through reviewed GitHub operations and rerun exact-head checks.

## Runtime-only tasks that cannot be certified by source edits

- Run the updated manual VPS diagnostic workflow and inspect its short-retention artifact.
- Confirm the deployed Git SHA, service state, resource pressure and deployment marker from the VPS without mutation.
- Capture three runtime-health samples and a five-minute dashboard WebSocket cadence sample.
- Repeat during market hours and off-market with explicit session labels.
- Run PostgreSQL concurrency, crash/restart, broker certification and recovery drills in a controlled non-production environment.

## Release decision

**Live remains OFF. No live order, broker credential login, service restart, VPS deployment, DNS change, or Vercel cutover is authorized by this tracker.** Batch 0 is complete only when all implementation gates and runtime evidence above are closed and reviewed.
