# Batch 0 — Runtime Metrics and WebSocket Baseline Runbook

**Status:** Source-derived runbook; no production runtime sample collected in this change.  
**Audited source:** `main` at `8da655f0c287251cf1602cae484714a7c26fcf15`.  
**Scope:** Read-only measurement only. Do not change subscriptions, scanner settings, runners, or order mode.

## 1. What the current code actually exposes

| Surface | Current contract | What it can establish | Limitation |
|---|---|---|---|
| `GET /health` | API/database health | Process responds and database check reports connected | Not proof that market-data sockets are healthy |
| `GET /api/v1/market-data/runtime/health` | Process-local `common_feed`, provider-readiness snapshot, runner snapshots, scanner counters | Feed age/status, exchange freshness, socket-group state, active instruments, runner activity | Snapshot belongs to the responding API process; it is not a fleet-wide view |
| `GET /api/v1/diagnostics/errors?limit=50` | In-memory diagnostics since process start | Recent captured backend/feed/runtime errors | Volatile; restart clears history; not a durable incident log |
| `GET /api/v1/market-data/live-health` | Used by frontend dashboard | Runtime feed health badge | Dashboard maps runtime age to a 5-second freshness threshold |
| `GET /api/v1/market-data/overview` | Used by frontend dashboard | Index quote and market-session overview | Quote freshness must be interpreted from each row's own status/timestamp |
| `WS /ws/dashboard` | One server-side dashboard stream; snapshot emitted about once per second | Dashboard snapshot cadence and integration counts | Not a per-tick latency feed; no broker socket is opened per browser |

The runtime-health route registers read-only provider-readiness observers once per shared manager instance, calls `manager.snapshot()`, then adds primary exchange freshness for required exchange types `[1, 2]`. It returns runner snapshots for cash-future, calendar, synthetic, and Box Spread. This route is diagnostic: it must not be used as a control endpoint.

The dashboard WebSocket reads existing scanner snapshots and sends `dashboard_snapshot` messages approximately once per second. It reports `live_orders: "OFF"`. It is a dashboard aggregation stream, not the market-data transport and not a substitute for the Box/IV scanner's own live channels.

## 2. Safe collection sequence

Run only against the currently deployed host, from the existing read-only diagnostic workflow or an approved read-only shell. Do not run bootstrap, restart systemd, pull Git, or change configuration during baseline collection.

1. Record UTC and IST collection start/end times, deployed Git SHA, service MainPID, ActiveState/SubState, NRestarts, MemoryCurrent/MemoryPeak, disk free space and inode availability.
2. Fetch `/health`; retain HTTP status, response body, request duration.
3. Fetch `/api/v1/market-data/runtime/health` three times, 10 seconds apart. Preserve the complete JSON privately; redact account identifiers, tokens, and provider secrets before sharing.
4. For each sample, record `server_timestamp`, `common_feed.status`, `common_feed.age_seconds`, `common_feed.exchange_freshness`, `common_feed.required_exchange_types`, `active_instruments`, `connected_groups`, `required_socket_groups`, `missing_socket_groups`, `disconnected_groups`, and per-group socket health where present.
5. Record each runner's `running` state, update/payload counters, last activity/timestamps, and diagnostic state. Do not infer a stopped runner from a missing optional snapshot.
6. Fetch `/api/v1/diagnostics/errors?limit=50`; record count and newest timestamps/severity/component. This endpoint is process-memory only.
7. Fetch `/api/v1/market-data/live-health` and `/api/v1/market-data/overview`; preserve the runtime tick age/status and per-index quote status separately.
8. Open one dashboard WebSocket client and measure connect time, inter-message arrival intervals, server timestamp age, disconnects, and reconnects for at least five minutes. Do not create multiple load clients against production.
9. Repeat the same sample outside market hours and during a controlled market-hours observation window. Label market session explicitly; closed-market silence is not a feed failure.

## 3. Metrics and interpretation

| Metric | Calculation / interpretation |
|---|---|
| API request latency | Client monotonic end minus start; report p50/p95/max over repeated probes |
| Snapshot freshness | Client receive time minus server timestamp; retain server/client clock offset estimate |
| Tick age | Use backend `common_feed.age_seconds` and exchange-specific freshness; never substitute dashboard message age |
| WebSocket cadence | Inter-message interval distribution; expected dashboard publisher cadence is approximately 1 second, not a guarantee of tick cadence |
| Socket availability | Connected required groups divided by required groups; also list missing/disconnected groups |
| Feed coverage | Active instruments plus required/connected socket groups; compare only across identical configured universes |
| Runner activity | Counter delta divided by elapsed seconds; a zero delta is not automatically a fault outside market hours |
| Error rate | New diagnostic errors grouped by component/severity during the sampling window; account for process restart reset |
| Resource pressure | MemoryCurrent/Peak, disk bytes/inodes, process restarts; compare with host capacity and previous samples |

Do not claim end-to-end scanner latency from these endpoints alone. A true tick-to-opportunity latency measurement needs a common trace/correlation timestamp carried from normalized market event through scanner evaluation and page publication. If that trace is not already emitted, record it as a future observability requirement rather than estimating it from unrelated timestamps.

## 4. Required isolation and safety checks

- Do not add market-data subscriptions or create a second broker WebSocket to measure health.
- Do not call runner ON/OFF, broker connect, order, cancel, or configuration mutation endpoints.
- Do not use the dashboard WebSocket as a source of executable signals.
- Keep Box Spread, IV Scanner, and IV Skew Flip configuration unchanged during measurement.
- Keep Live Trading OFF. Runtime health does not certify broker order readiness.
- If an endpoint returns an unexpected schema, record the exact HTTP status and sanitized body; do not silently treat missing fields as healthy.
- If the API runs multiple workers, collect each worker identity separately. The current diagnostics and page-push state are process-local; one worker's response cannot certify all workers.

## 5. Existing workflow gap

The existing `.github/workflows/vps-read-only-diagnostic.yml` is read-only, but its inspected commands probe legacy `common-feed-health` and Cash-Future endpoints and do not currently collect the full `/api/v1/market-data/runtime/health` payload or Box runner diagnostics. This runbook defines the required measurement set; it does not modify or dispatch that workflow.

Before automating collection, update the diagnostic workflow in a separate reviewed change to:
- capture runtime-health JSON and diagnostics errors with bounded timeouts;
- capture service/Git/resource metadata without changing state;
- store redacted output as a restricted artifact with a short retention period;
- fail visibly on HTTP/schema errors while still preserving partial diagnostics;
- never print secrets or full broker credential/session objects.

## 6. Evidence still required to close Batch 0

- Three consecutive runtime-health samples from the actual VPS.
- At least one five-minute dashboard WebSocket cadence sample.
- Market-hours and off-market comparison with session labels.
- Box and IV runner snapshots, feed freshness, and socket-group health from the deployed process.
- Current VPS Git SHA/service/resource state, because the earlier SSH deployment timeout left remote state unknown.
- A PostgreSQL concurrency/migration rehearsal and a separate crash/recovery test report.

**Decision:** Source-level observability is sufficient to define a safe read-only baseline, but no production latency, feed-health, or VPS-readiness claim is made until those runtime samples are collected.