# VPT-X and live-backend load review (initial static audit)

Date: 2026-11-22

## Scope / safety
This is a read-only static review of the current `main` backend plus the isolated VPT-X PR branch. It does not inspect live server telemetry or open broker connections. No feed, scanner, runner, deployment, or existing page was changed by this audit.

## Executive assessment
The backend is already a multi-runner live application. The startup/lifespan and shared `main.py` are a coordination-heavy area, so adding VPT-X as a new feed/runner now would increase operational risk. The first VPT-X evaluator should remain pure and snapshot-fed until load budgets, data quality, replay calibration and feed ownership are designed.

**Static review does not prove the live backend is currently healthy or within CPU/RAM/network limits.** That requires non-invasive runtime telemetry (CPU, RSS, event-loop lag, queue depth, websocket clients, quote rates, scanner durations, DB latency) collected under market hours.

## Findings

### 1. Existing backend has a large startup/runtime surface
`backend/app/main.py` imports/coordinates Box Spread, Cash-Future, Calendar Spread, Synthetic, IV Skew Flip, Weekly Swing, Shoonya supervisor, paper ledger/websocket and multiple recovery loops. This is a high coupling/ownership risk for adding another live task directly in this file.
- Recommendation: future VPT-X wiring must use a separate lifecycle owner, explicit OFF-by-default config, bounded universe, single shared tick observer, and no independent duplicate subscriptions.
- Risk: adding a new polling loop or per-strategy socket/worker in `main.py` can amplify event-loop, network and memory pressure.

### 2. Box Spread scanner pair search is bounded in normal mode, but has an unbounded-by-policy fallback
`backend/app/scanner/box_spread.py` normal mode calls `enumerate_box_pairs` with policy distances (stock 3–5, index 3–15). This limits candidate pair generation.
- `include_all_strike_pairs=True` enumerates every ordered strike pair, quadratic in number of strikes. Keep this diagnostic path out of the live hot path; enforce a cap if it can be called by any live consumer.
- Per-snapshot loop performs pair checks and both LONG/SHORT evaluations; scan time should be measured by underlying/expiry and quote count.

### 3. Snapshot assembly contains sync work and DB-backed reads
Box page push snapshot composes opportunity data, spot data, runner/feed status, index selection and cached shadow panel, plus edge timing aggregate. Shadow snapshot has a one-second cache; edge timing recomputes at most every 30 seconds per code comments. Ensure these are cached/read-only and never moved into quote-tick callbacks.
- Recommendation: push hot quote deltas separately; refresh heavier diagnostics on a slow cadence and include revision/age fields.

### 4. Blocking work is intentionally isolated, but spawned daemon workers must stay data-only
`run_blocking_daemon` moves blocking work to a daemon thread and is documented for fetch-only work. This is a useful shutdown safeguard, but it does not cancel a blocked HTTP request; do not use it to create sockets, mutate runner state, or start tasks.
- Recommendation: bound concurrent fetches and expose worker counts/timeouts.

### 5. Config defaults show several opt-in runners, not an automatic CPU budget
Many live scanners are controlled by individual enable flags; defaults in code are mostly OFF. Feature flags prevent unwanted starts but do not guarantee safe resource allocation when several are enabled together.
- Recommendation: add a shared runtime budget/health view before enabling another continuous scanner: active runners, per-run scan duration, tick throughput, dropped/coalesced events, queue depth/high-water, event-loop lag, RSS and DB write latency.

## Proposed backend guardrails before VPT-X becomes live
1. **Single ingestion fan-out:** reuse already-authorized tick events; never duplicate broker connections.
2. **Bounded state:** max ticks/window per instrument, TTL eviction, max instruments, max signal candidates; no unbounded history.
3. **Compute cadence:** microstructure calculations on coalesced 250ms/1s buckets; 5s validation; slow regime context (30–60s); not full recompute for every websocket frame.
4. **Incremental features:** maintain rolling sums/extrema/flow counters rather than scanning the full history each tick.
5. **Backpressure:** drop/coalesce stale intermediate updates; preserve latest authoritative state; explicit queue overflow counters.
6. **CPU isolation:** no expensive loops, DB writes or logging per tick in the event loop; bounded worker pool only if measured necessary.
7. **Data validity:** quote age, timestamp monotonicity, spread/depth validity, sequence gaps, missing feed and clock-skew gates fail closed.
8. **Observability:** per-runner p50/p95/p99 scan time; event-loop lag; RSS; incoming ticks/s; stale drops; websocket subscriber count; queue depth; DB p95; restart/error count.
9. **Safety:** VPT-X remains scanner-only, no broker integration or trade execution until separate authorization and independent review.

## Load test plan (not run against live service)
- Synthetic tick replay at 1x, 5x and 20x intended peak rate.
- NIFTY + BANKNIFTY together; test three pattern families, both directions.
- Compare baseline backend and VPT-X evaluator CPU time/RSS/event-loop lag.
- Long run (at least 60 minutes) for memory leak/TTL eviction.
- Burst + reconnect + timestamp disorder + missing book/flow fields.
- Acceptance targets to be agreed after measuring baseline; do not invent current capacity.

## VPT-X status
Initial heuristic thresholds are research placeholders, not calibrated. The engine is isolated, pure, no broker feed/subscription, no API/UI, no order routing. CI and replay quality gates are still required.
