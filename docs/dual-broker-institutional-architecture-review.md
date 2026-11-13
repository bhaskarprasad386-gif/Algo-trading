# Dual-Broker Institutional Market-Data Architecture — Deep Review and Batch Addendum

Status: reviewed design proposal only. This document does not authorize production provider selection, strategy fan-out, trading, deployment, or live broker activation.

## 1. Executive decision

Adopt two independently supervised broker acquisition engines, a broker-neutral processing plane, and isolated dashboard fan-out. Do not hard-code Shoonya as primary or Angel One as secondary until instrument-level live shadow evidence proves coverage, freshness, depth, stability, and account-specific limits.

Non-negotiable rules:
- Angel One and Shoonya keep independent authentication, socket lifecycle, heartbeat, reconnect, subscription registry, admission controls, and health.
- A common contract identity and canonical quote contract allow safe comparison.
- Acquisition callbacks do minimal validation and non-blocking enqueue only.
- Calculation, persistence, alerts, and UI delivery stay outside transport callbacks.
- Failover is per instrument, not based only on a broker-wide heartbeat.
- Existing Box Spread and IV Scanner calculations, subscriptions, entry/exit rules, and current Angel behavior remain unchanged until parity and approval.

## 2. Critical corrections to the supplied proposal

### Broker capacity
- The current Shoonya adapter and settings enforce a hard maximum of 500 tokens per WebSocket connection. A 2,000+ token design cannot be achieved by raising a config value; it requires broker-confirmed multi-connection support, session-limit review, and a separately tested shard supervisor.
- The current Angel CommonWebSocketManager enforces at most 3 sockets and 1,000 tokens per socket. The Box + IV budget must remain global.
- Software caps are safety limits, not promises of broker-side capacity or API quota. Confirm current broker documentation and account entitlements before changing them.
- Do not promise zero rate limits or no ping/pong timeouts. The design can reduce avoidable pressure and recover safely, not eliminate broker/network limits.

### Primary / secondary and failover
A global heartbeat proves transport activity, not that a particular option has a fresh valid quote. Source selection must be per canonical instrument and consider:
- authenticated session and current connection generation;
- last inbound frame and last valid quote ages;
- exchange timestamp and local monotonic receive timestamp;
- contract identity, expiry, strike, option right and segment;
- bid/ask validity, sizes, crossed/locked market checks and depth completeness;
- malformed, out-of-order, queue-overflow and delivery-error counters;
- source priority only after live broker comparison.

Use explicit states: PRIMARY_FRESH, SECONDARY_FRESH, BOTH_STALE, IDENTITY_MISMATCH, NO_VALID_QUOTE. Add hysteresis to prevent source flapping. Never overwrite a fresh quote with an older quote. Never combine bid from one broker and ask from another into an executable synthetic quote unless a separately reviewed policy permits it and labels every field's source.

### Non-blocking acquisition
Shoonya already has bounded tick and order queues with dedicated dispatch workers. Review its callback path to keep transport callbacks limited to epoch/frame validation, envelope creation and put_nowait. Angel CommonWebSocketManager._on_data() currently invokes raw callbacks and normalization/record callbacks inline after releasing its lock; this is a concrete head-of-line blocking risk.

Target callback steps:
1. Verify socket generation and minimal frame shape.
2. Capture broker, connection generation, exchange, token, source timestamp and local monotonic received_at_ns.
3. Copy/freeze payload; do not share a mutable SDK dict across threads.
4. Enqueue non-blockingly into a bounded broker-specific ingress queue.
5. Return immediately.

Queue full is a feed-integrity fault: count it, mark only the affected broker/shard degraded and trigger controlled recovery/resync. Never silently drop while reporting LIVE. Keep order lifecycle events in a separate loss-sensitive queue. Do not claim a fixed 0.05 ms callback time; measure p50/p95/p99 under representative load.

### Redis
Redis is an optional shared-state layer, not the first prerequisite. Start with bounded in-process queues and a provider-neutral cache interface while the app remains single-process. Introduce Redis when cross-process ownership or multiple services actually require shared state.

If Redis is added: use pooled connections, bounded pipeline batches, explicit timeouts, per-broker/per-instrument namespaced keys, TTLs, source timestamps plus local receive timestamps, queue/backpressure handling, and defined Redis-outage behavior. Broker callbacks must never block on Redis. Update broker heartbeat from transport/session health, not only tick arrival. Pub/Sub is ephemeral: dashboard reconnect must receive a current snapshot before updates. Use Streams only if replay/durable delivery is required. Benchmark throughput and end-to-end latency before capacity claims.

### Calculation and NumPy
Separate CPU-heavy calculations from acquisition. Do not assume NumPy makes existing Box/IV work 2–4 ms, and do not rewrite scanner formulas as part of transport work. Profile instrument count, update rate, batch size, calculation duration, allocation/GC, stale rejection and p50/p95/p99 latency. Keep existing calculations as parity baseline. Vectorization must pass deterministic replay parity before shadow comparison. Use process workers only where profiling justifies them; queues must remain bounded.

### Dashboard WebSocket
FastAPI push is appropriate, but every browser needs a bounded outbound queue and slow-client policy. One slow browser must not block other clients or scanners. Coalesce replaceable latest-quote UI updates by instrument; never coalesce alerts or order lifecycle events. Send initial snapshot, sequence/version metadata, health and reconnect/resync behavior. Browser rendering can batch with animation-frame or 100–250 ms updates without altering backend signal timestamps.

### Services and SQL
Do not immediately split into four systemd services. Broker ownership is process-local today; splitting before singleton ownership, IPC, Redis recovery and leader fencing can create duplicate sessions and subscriptions. Keep single-owner process until ownership and data-plane contracts are tested. Split later with health dependencies and restart policies.

SQL pool_size=20 and max_overflow=30 are not universal fixes. Keep live tick processing out of synchronous request/scanner loops, but size pools from database type, worker count, connection limits and measured concurrency. SQLite does not share network-database pool semantics.

### Contract master and auto-login
Canonical contract identity must include underlying/root, exchange/segment, instrument type, expiry, normalized strike, CE/PE, lot size, tick size and tradability metadata. Keep separate Angel and Shoonya token mappings. Validate duplicates, missing fields, expiry/strike scaling, lot/tick mismatches, inactive contracts and stale masters. Record source version/hash/fetch time; atomically publish only after validation. Quarantine ambiguous contracts; never guess mappings.

Schedule master refresh from exchange/broker calendar and actual file availability, not a hard-coded 08:50 assumption. Support holidays, retries, partial downloads, checksums and rollback to last known-good snapshot.

Auto-login means controlled independent session lifecycle using each broker's supported auth flow and secret store. Never commit credentials, TOTP seeds, passwords or access tokens; never log them or scrape browser sessions. Re-auth must fail closed. Master refresh and session login are separate jobs.

## 3. Target architecture

Angel One shared manager (3 sockets x 1,000 max) -> generation-guarded bounded Angel ingress queue
Shoonya independent supervisor (500 current cap) -> generation-guarded bounded Shoonya ingress queue
Both queues -> canonical contract identity + quote cache -> per-instrument source quality/failover selector -> existing Box and IV processing adapters -> snapshot/event cache -> bounded FastAPI WebSocket fan-out -> dashboard.

Independent health, metrics, replay and recovery controls surround both acquisition engines. Broker acquisition and scanner calculation are separate engines. Shoonya failure must not stop Angel; Angel failure must not stop Shoonya. Source selection cannot mutate broker subscription lifecycle.

## 4. Expanded batch plan

This expands the previous Batch 0–10 plan; it does not discard or silently mark earlier batches complete.

| Batch | Deliverable | Acceptance gate |
|---|---|---|
| 0 | Existing architecture audit | Socket ownership, limits, callbacks, flags and regression baseline documented |
| 1 | Angel shared manager for Box + IV | IV stop cannot close application-owned manager |
| 2 | Angel global three-socket admission | Box/IV obey one manager-wide budget and cooldown |
| 3 | Shoonya independent lifecycle | Default-off, isolated registry, auth-gated subscriptions, bounded shutdown |
| 4 | Heartbeat/reconnect hardening | Epoch-safe callbacks, bounded reconnect, no duplicate workers |
| 5 | Canonical contract master + dual-broker token map | Deterministic keys, validated masters, atomic versioned swap, conflict quarantine |
| 6 | Dual acquisition ingress | Both brokers enqueue immutable envelopes non-blockingly; bounded queues and overflow alarms |
| 7 | Canonical quote cache / optional Redis adapter | Per-broker/per-instrument snapshots, TTL, source timestamps, Redis outage tested |
| 8 | Per-instrument source quality and failover | No stale overwrite/flapping; identity/depth checks and recovery hysteresis |
| 9 | Calculation worker isolation | Acquisition unaffected by blocked calculation; Box/IV replay parity |
| 10 | FastAPI dashboard WebSocket fan-out | Initial snapshot, bounded client queues, slow-client isolation, reconnect/resync |
| 11 | Session/master lifecycle | Independent broker auth refresh, secure secrets, scheduled master refresh and rollback |
| 12 | Service split and deployment | Only after singleton ownership, IPC, fencing and monitoring are proven |
| 13 | Soak, fault-injection and promotion gates | Market-hours shadow soak, load tests, broker outage simulation, parity and no-go criteria |

## 5. Required tests and metrics

Functional tests:
- Same contract maps to different broker tokens but one canonical key.
- Expiry, strike precision, CE/PE, lot/tick, exchange and segment mismatch rejection.
- Duplicate/ambiguous keys quarantined.
- Stale-generation and out-of-order frames rejected per instrument; equal timestamps may carry legitimate quote updates.
- Source selection chooses only fresh valid matching contracts and recovers with hysteresis.
- Stale Angel quote never overwrites fresher Shoonya quote, and vice versa.
- Missing bid/ask/depth never appears executable.
- Queue saturation degrades only the affected broker/shard.
- Redis outage does not block acquisition; reconnecting UI receives fresh snapshot.
- Slow calculation and slow dashboard clients do not block transport or other consumers.
- Auth expiry/rejection and master download failure retain last known-good data and fail closed.

Expose per broker/shard: connection/auth state, generation, subscribed token count/capacity, reconnect count, last frame age, last valid quote age, ping/pong latency where observable, queue depth/capacity/high-water, overflow count, callback-duration histogram, processing lag, rejected/out-of-order frames, per-consumer lag/errors, cache age and dashboard client drops.

Measure end-to-end receive -> canonical quote -> scanner output -> UI render latency at p50/p95/p99. Do not advertise <5 ms until measured on the target VPS under load.

Promotion gates:
- Backend and integration CI green on exact commit.
- Deterministic replay parity for existing Box and IV output.
- Market-hours shadow comparison without scanner source switching or orders.
- No unresolved identity mismatch, queue overflow, unexplained quote divergence or shutdown timeout.
- Explicit operator approval before source priority/failover is enabled.
- CI or preview does not imply production activation.

## 6. Current implementation constraints and immediate next work

- Shoonya is default-off and shadow-only; it currently enforces 500 tokens per connection.
- Angel Box + IV shared manager PR #479 is open; backend CI must finish before merge.
- Shoonya PR #480 is draft; latest stale-auth and repeated-start regression tests need CI on exact head.
- Angel callback fan-out is still synchronous in CommonWebSocketManager._on_data(); this is the first concrete acquisition-side gap for Batch 6.
- Do not add Redis, NumPy rewrites, primary/secondary selection, cross-broker scanner failover or separate systemd units in one change.
- No live broker validation, VPS deployment or production promotion is claimed by this design document.

## 7. Batch 5 implementation progress (code added; CI pending)

Added `backend/app/market_data/provider_contracts.py` as an additive provider-neutral mapping layer. It deliberately does not replace `InstrumentRegistry`, alter `InstrumentKey`, or subscribe either broker.

Implemented safeguards:
- Canonical contract key is independent of broker token and normalizes exchange, segment, underlying, instrument type, expiry, Decimal strike and CE/PE.
- Each broker retains its own exchange/segment/token tuple; the same canonical contract can resolve to different Angel One and Shoonya tokens.
- One broker token cannot map to multiple canonical contracts; duplicate mappings from the same broker are rejected.
- Cross-broker lot-size and tick-size disagreement rejects the candidate snapshot.
- Inactive contracts are retained as metadata but excluded from tradable resolution by default.
- Candidate rows and metadata are validated before one lock-protected snapshot pointer swap; empty/invalid/stale candidates leave the last-known-good snapshot untouched.
- Snapshot stores source version, SHA-256 digest and timezone-aware fetch time. When source bytes are available, callers should pass the source-file digest; otherwise the registry computes a deterministic mapping fingerprint.

Added `backend/tests/test_provider_contracts.py` for dual-token identity, strike precision, duplicate provider keys, token collision, lot/tick mismatch, inactive mapping, empty/stale rollback and incomplete option identity.

This is a foundation, not yet connected to broker master downloaders. No production mapping is loaded, no scanner consumes it, and no live subscriptions or provider selection change in this batch. Next Batch 5 step is to add provider-specific parsers and integrity gates using actual Angel/Shoonya master schemas, then validate representative real master fixtures before wiring the atomic snapshot publisher.

## 8. Batch 5 — parser implementation addendum

Added:
- `backend/app/market_data/provider_master_parsers.py`
  - Angel One OpenAPIScripMaster JSON parser; documented Angel option strike normalization divides the published strike integer by 100.
  - Shoonya CSV/pipe-delimited parser; strike scaling is an explicit caller argument and is never guessed from magnitude.
  - Alias normalization for provider column headers, ISO/common Indian expiry formats, CE/PE validation, positive integer lot sizes, positive finite Decimal tick/strike values, tradability flags, and SHA-256 of original source bytes.
  - Row-level quarantine report with source row number and reason. Duplicate provider token rows are quarantined.
- `backend/tests/test_provider_master_parsers.py`
  - Angel scaled strike, Shoonya pipe schema with case-sensitive token, explicit scale failure, malformed/duplicate row quarantine, invalid payload/delimiter tests.
- `BrokerContractToken` now preserves token case. Broker, exchange and segment remain normalized uppercase; token identifiers are treated as opaque provider values.

Important operational boundary:
- No downloader, login, secrets, cron, or live master refresh is invoked by these parsers.
- Shoonya strike scale and delimiter must be confirmed against a captured official master file for the account/segment before production use. The parser deliberately does not infer scale.
- The report separates accepted and quarantined rows; a later publisher must apply a reviewed per-source completeness threshold. Never publish a partial master merely because some rows parsed.
- Angel One and Shoonya schemas vary by instrument family and file revision. Current tests are synthetic fixtures, not evidence that every live provider row family is covered.
- Parser output is not wired to `InstrumentRegistry`, Box Spread, IV Scanner, or broker subscriptions. Existing live behavior is unchanged.

## 9. Batch 5 expanded implementation — dual-master gate

The parser layer now includes a guarded dual-source publication function:
- Requires exactly one Angel One report and one Shoonya report.
- Rejects empty reports and reports with no accepted rows.
- Applies a configurable maximum row rejection ratio (default 2%); the caller must tune this only after measuring the exact official master schema and scope.
- Combines both original source-file SHA-256 values into a deterministic snapshot digest.
- Calls the registry's single atomic replacement only after both source reports pass. A broker missing, rejected source, stale timestamp, duplicate mapping, or cross-broker lot/tick mismatch leaves the previous snapshot intact.
- Keeps provider tokens opaque and case-sensitive. Broker, exchange and segment are normalized separately.

Parser precision:
- Angel One option strike and tick size are divided by 100 based on the published integer-scaled master representation; this assumption is encoded in the parser and fixtures, not dynamically guessed.
- Shoonya strike and tick scales are explicit call parameters (default 1). Confirm the official file's exact header, segment, strike and tick representation before setting production values.
- Angel CE/PE may be obtained from an explicit option-right field or the exact trading-symbol suffix. No substring heuristics are used.
- Synthetic tests cover the expected JSON/CSV layouts and atomic rollback. They are not a substitute for capturing and validating current official master files from both brokers.

Do not connect this publisher to startup or periodic jobs yet. The next integration gate requires official sample files for the relevant NFO/BFO/MCX segments, explicit supported instrument-family scope, measured quarantine rates, and a dry-run report showing canonical pair coverage and every mismatch category. No scanner or broker subscription is wired to the new registry.
