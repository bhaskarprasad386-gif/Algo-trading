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

## 10. Batch 5 dry-run coverage audit

Added `backend/app/market_data/provider_master_audit.py`:
- Compares canonical contracts present in both broker reports before publication.
- Reports common, Angel-only and Shoonya-only contracts, one-sided coverage ratios, cross-broker lot/tick mismatches, and duplicate canonical keys within a broker report.
- Uses deterministic canonical-key ordering. It is a read-only audit and never mutates the registry.

Added tests for common/one-sided coverage, metadata mismatch and missing broker report. The audit makes incomplete broker coverage visible rather than silently implying every contract has a dual feed. One-sided contracts may remain usable from their single provider in a future design, but cannot be treated as failover-capable.

Batch 5 remains **not production-ready** until real official files are supplied/collected through an approved secure workflow, the provider-specific scales and segment mapping are confirmed, and the complete dry-run audit has been reviewed. Do not relax rejection thresholds merely to make a download publish.

## 11. Final Batch 5 integrity review and remaining blockers

Additional validation refinements:
- Angel One fixtures now use the documented integer-scaled strike/tick convention and CE/PE trading-symbol suffixes; Shoonya token case is preserved exactly.
- Canonical futures identities now require expiry, just as option identities require expiry, strike and option right.
- A separate coverage audit identifies common versus one-sided contracts and lot/tick mismatches before the atomic publisher is called.
- Tests include last-known-good rollback when a broker report is absent, a report exceeds the configured quarantine ratio, or cross-broker metadata conflicts.

**Not yet verified:** GitHub Actions has not produced a workflow run for the latest head. The current status check visible on the commit is Vercel; its failure is a deployment-rate-limit status, not a backend test result. Synthetic fixtures do not establish that current live Angel/Shoonya master headers and numeric scales are correct for every supported segment.

**No integration activation:** no scheduled fetch, broker authentication, database persistence, Redis, scanner fan-out, WebSocket subscription change, source failover, order routing, VPS deployment or merge has been performed.


## 12. Batch 6 — bounded provider ingress primitives

Added `backend/app/market_data/provider_ingress.py` as an additive, currently unwired transport boundary:
- Immutable provider envelope carries provider, connection generation, exchange/token, event type, provider timestamp, wall-clock receive time and monotonic receive time.
- Payloads are detached from the callback's original object and recursively frozen so nested quote/depth structures cannot be mutated after enqueue.
- Independent bounded FIFO lanes are configured separately for market ticks and order lifecycle events. No coalescing or silent tick overwrite is implemented.
- Publish is non-blocking. A full queue increments overflow and latches an explicit integrity fault; the caller must degrade the affected provider/shard rather than continue claiming healthy data.
- Generation fencing rejects late callbacks from retired connections. The consumer-side `get_current()` also discards already-queued frames from older generations after reconnect.
- Snapshot exposes capacity, depth, accepted, wrong-provider, stale-generation, stale-dequeued, overflow and integrity-fault counters.

Regression tests cover queue overflow, stale callbacks, queued stale frames, provider mismatch, close behavior, generation monotonicity, independent market/order lanes, and nested payload immutability.

**Integration intentionally deferred:** Neither Angel nor Shoonya socket callbacks are wired to this queue yet. No scanner receives these envelopes, no subscription behavior changes, and no production runtime setting changes. The next integration step must add transport-generation ownership at each broker callback boundary, preserve existing callback consumers during migration, and test shutdown/drain behavior before any live connection is switched over.


## 13. Batch 7 — ingress worker lifecycle foundation

Added a single-owner `ProviderIngressWorker` for the Batch 6 bounded lane:
- Worker invokes a supplied downstream handler outside the transport callback.
- Handler exceptions are counted and isolated; one bad consumer event does not kill the worker loop.
- Graceful shutdown closes publication first and drains already-accepted current-generation frames.
- Explicit non-draining shutdown discards pending frames and increments a separate counter; no silent discard.
- A single monotonic shutdown deadline bounds the full stop operation, rather than multiplying timeout across sequential joins.
- Dequeue generation checks remain active during drain, so reconnect-retired frames are discarded and counted.

Tests cover FIFO drain, handler exception isolation, non-draining discard accounting and the previously added queue/generation invariants.

**Still deliberately not wired:** The Angel manager's synchronous callback contract and Shoonya's existing authenticated normalization/dispatch path remain unchanged. This worker is a testable integration primitive only. Before connecting it to a broker, a separate migration must prove per-shard ordering, queue capacity under observed peak rates, overflow degradation/recovery, and callback compatibility. No Box/IV consumer or trading route has been switched.

## 14. Batch 8 — callback-boundary integration review (no live-path switch)

Reviewed the current Angel One CommonWebSocketManager._on_data() path and Shoonya ShoonyaWebSocket dispatch lifecycle before attempting ingress integration.

### Angel One findings
- _on_data() rejects callbacks whose socket generation is no longer current, then parses/routes the frame under the manager lock.
- It invokes registered raw callbacks and normalized record callbacks inline on the transport callback thread after releasing the lock.
- This is a real head-of-line blocking surface: a slow consumer can delay processing of later frames on that socket callback thread.
- Existing consumers and tests may rely on synchronous callback delivery. Replacing it globally with a worker would silently change ordering/timing and exception visibility; therefore this batch does not change the default delivery contract.

### Shoonya findings
- Shoonya already has a per-connection feed epoch and callback guard to suppress retired-socket callbacks.
- Market ticks and order updates already use separate bounded queues and dedicated dispatch loops.
- Queue overflow is surfaced as a feed/order integrity fault and initiates controlled socket recovery.
- Adding BoundedProviderIngress as a second queue in front of these queues would duplicate buffering, complicate shutdown accounting, and increase latency without a demonstrated need. No second queue was added.

### Batch 8 safety decision
- Keep Angel synchronous delivery and Shoonya's existing dispatch queues unchanged by default.
- Do not route either scanner through the new provider ingress worker yet.
- Any Angel migration must be explicitly opt-in and per-shard, preserve FIFO order for a shard, preserve callback payload shape, expose queue overflow as degraded health, and support a shadow comparison before consumer cutover.
- Any Shoonya migration must first prove that the existing queues can be adapted without double buffering; otherwise retain the current dispatch design and instrument it directly.
- Before any live-path switch, add compatibility tests for raw and normalized callbacks, per-shard ordering, stale-generation rejection, overflow recovery, consumer exceptions, and stop/restart lifecycle. Compare callback latency and downstream output parity under replay.

### Additional worker lifecycle regression
- Added a deterministic test where a handler remains in-flight beyond the shutdown deadline. stop() must raise TimeoutError rather than claim success; after the handler is released, a subsequent stop call must reap the worker cleanly.
- This documents an unavoidable Python thread limitation: a running handler cannot be forcibly cancelled safely. Shutdown remains bounded from the caller's perspective and reports the timeout explicitly.

**Result:** Batch 8 review is complete as an architecture/safety gate only. No broker callback, subscription, Box Spread/IV Scanner consumer, order route, or production setting changed. No tests are claimed as executed or passing.

## 15. Batch 9 — opt-in Angel per-shard callback adapter foundation

Added backend/app/market_data/angel_callback_dispatch.py as a standalone adapter, deliberately not instantiated by CommonWebSocketManager:
- One adapter instance represents one socket shard and owns one bounded FIFO queue and one consumer worker.
- Each queued batch preserves callback order within that shard; raw and normalized callback payload shapes are passed through unchanged.
- Payloads are deep-copied before crossing the thread boundary so later SDK mutation cannot rewrite queued data.
- Generation fencing rejects stale publishes and drops already-queued retired-generation batches at dispatch time.
- Queue overflow increments a counter and latches an integrity fault; callers must not report the shard as healthy after overflow.
- Callback exceptions are isolated and counted; remaining callbacks in the batch continue in order.
- Graceful stop closes publication and drains accepted batches within one deadline; timeout is surfaced explicitly.

Added backend/tests/test_angel_callback_dispatch.py for FIFO ordering, stale-generation rejection, overflow integrity fault, callback exception isolation, shutdown behavior and mutable payload detachment.

**Integration remains disabled:** CommonWebSocketManager._on_data() still invokes callbacks synchronously. This adapter has not been connected to socket lifecycle, manager health, or scanner consumers. Before opt-in wiring, the manager must own one adapter per SocketGroup, activate it with the exact socket generation, close/drain it before replacing a shard, and propagate adapter overflow into that shard's health state. No Box Spread or IV Scanner behavior changed. Tests have not been executed in this environment.


## 16. Batch 11 — per-shard delivery accounting and overflow health contract

Hardened the standalone Angel shard adapter before manager integration:
- Tracks callback attempts, successful callbacks, callback exceptions, processed batches, stale batches, queue overflow, payload-copy failures, and discarded batches independently.
- Snapshot exposes a shard-local `delivery_status`: `DEGRADED` when the queue-integrity fault is latched or any callback failed; otherwise `HEALTHY`.
- A payload-copy failure rejects the whole batch before enqueue and latches the integrity fault. It cannot escape into the broker callback boundary as an uncaught exception.
- Non-draining shutdown counts discarded queued batches. Graceful stop when no worker was started also accounts for accepted-but-undeliverable batches rather than leaving them hidden in the queue.
- Added deterministic tests for attempt/success/error accounting, overflow degradation, and graceful stop without a worker.

### Recovery contract
- The integrity fault is intentionally sticky for the lifetime of an adapter. Do not clear it and resume the same queue as healthy.
- The manager integration must treat overflow or payload-copy failure as a shard-local fault, stop accepting callbacks for that generation, fence its queued work, and request the existing controlled shard replacement path.
- Callback exceptions are visible as delivery degradation but must not be confused with transport disconnect or automatically trigger broker reconnect unless an explicit reviewed policy says so.
- A replacement generation must receive a fresh adapter and fresh counters. Other shards must continue independently.
- No automatic retry/replay of rejected batches: the adapter cannot prove that replaying a market-data callback is idempotent.

**Result:** Batch 11 adds delivery observability and a precise recovery contract only. CommonWebSocketManager remains on synchronous delivery; no scanner, subscription, broker session, order route, or production behavior changed. Tests have not been executed; exact-head CI availability must be checked separately.



## 17. Batch 12 — fail-closed shard integrity semantics

Hardened the standalone Angel per-shard callback adapter before any manager wiring:
- Queue overflow and payload-detachment failure now retire the adapter generation immediately: publication closes, the generation fence advances, and pending queued batches are explicitly discarded and counted.
- Subsequent publishes are rejected; the failed adapter cannot be reactivated or reported healthy. A replacement socket generation must own a fresh adapter instance and fresh counters.
- A callback already dequeued and executing cannot be safely cancelled by Python; it may finish. All not-yet-started queued callbacks are fenced/discarded after the integrity fault.
- Payload copying is preceded by a generation/closed check and followed by a second check under the queue lock, so a concurrent retirement cannot enqueue a batch after the generation has failed.
- Added deterministic regression coverage for overflow retirement, queued-batch discard, rejected post-fault publication, payload-copy failure retirement, and the in-flight callback limitation.

**Manager integration remains intentionally deferred:** this batch does not add adapters to CommonWebSocketManager, alter _on_data(), schedule shard recovery, or change transport health reporting. The existing synchronous Angel callback contract and all Box Spread/IV Scanner consumers remain unchanged. No Shoonya queue changes, broker session changes, order routing, merge, deployment, or live validation occurred. Tests still require execution on the exact PR head.


## 18. Batch 13 — opt-in Angel manager callback-dispatch integration

Integrated the previously standalone per-shard callback adapter into `CommonWebSocketManager` behind an explicit opt-in constructor flag:
- `async_callback_dispatch=False` remains the default. Existing Box Spread / IV Scanner consumers therefore retain the synchronous callback contract unless an operator explicitly enables the new path.
- When enabled, each current `SocketGroup` and socket generation receives its own bounded `AngelShardCallbackAdapter`; the SDK callback only validates the current manager/adapter identity, detaches the raw message into the bounded queue, and returns.
- The existing manager routing, normalization, freshness accounting and consumer fan-out execute on that shard's worker. This isolates the full manager callback path, not only the final consumer callback.
- Adapter queue overflow or payload-copy integrity failure is fail-closed and schedules the existing generation-scoped socket-failure/recovery path from a separate thread, never from the SDK callback thread.
- The adapter is retired on all six manager socket-generation invalidation paths. Retirement removes it under the manager state lock but stops/joins its worker asynchronously, so no manager lock is held while waiting for callback code.
- Manager shutdown captures and stops adapters outside the manager state lock. A stuck consumer worker is bounded by a one-second retirement budget and emits a warning; Python cannot forcibly cancel an already-running consumer callback.
- `snapshot()` now exposes whether opt-in dispatch is enabled and per-shard queue depth, delivery status, counters and integrity fault state. A bounded history retains the last 12 degraded/retired shard snapshots, so overflow remains visible after the failed generation is removed from the active adapter map.

### Batch 13 validation boundary
- Added focused regression tests for synchronous default parity, opt-in worker-thread delivery, off-transport-thread overflow recovery, and non-blocking adapter retirement while the manager lock is held.
- The feature remains opt-in and has not been enabled in production. No existing Box Spread / IV Scanner subscriptions, scanner calculations, signal rules, or order routes were changed.
- This commit does not claim full end-to-end broker parity or production readiness. Exact-head CI and the complete backend regression suite must pass before considering activation.


## 19. Batch 14 — callback fault isolation and lifecycle regression coverage

Expanded the focused manager regression suite to cover:
- The manager's generation-checked failure/recovery boundary after an adapter integrity fault, with the recovery scheduling hook observed off the SDK callback thread.
- Rejection of late callbacks from a retired socket/generation after a replacement socket and fresh adapter have been installed.
- Isolation between two independent shard adapters: a failed shard requests its own recovery while the other shard continues delivering.
- Constructor validation for explicit boolean opt-in and positive bounded queue capacity.

The tests are committed but have not been executed in this environment. GitHub Actions currently has no workflow run associated with the checked PR head, so no CI pass is claimed. No production flag was enabled; Box Spread / IV Scanner routing and calculations remain unchanged.


## 20. Batch 15 — shadow-only provider quote quality contract

Added `provider_quote_quality.py` as a broker-neutral diagnostic primitive. It compares two independently supplied observations against the same canonical contract identity and reports only:
- `COMPARABLE`: both observations pass quote-integrity and freshness checks;
- `SINGLE_SOURCE_FRESH`: exactly one observation is currently fresh;
- `BOTH_STALE`: both observations are structurally valid but stale;
- `IDENTITY_MISMATCH`: canonical contract keys differ;
- `NO_VALID_QUOTE`: neither observation is valid.

Checks include authenticated/current connection generation, positive exchange timestamp, exchange-time age, local monotonic receive age (clock domains are never mixed), positive bid/ask and sizes, crossed market, and depth completeness. The result preserves provider-specific reasons and both age measurements.

Safety boundary:
- This is a pure, shadow-only contract; it is not wired into Angel or Shoonya callbacks, scanner subscriptions, strategy calculations, UI, or order routing.
- It does not rank providers, nominate a primary/secondary broker, merge quote sides, or execute failover.
- Exchange timestamps are assumed normalized to nanoseconds by the caller; no broker-specific timestamp parsing is inferred here.
- Tests are committed but not executed in this environment. Exact-head CI is still required before integration.


## 21. Batch 16 — read-only normalized quote adapter

Added a narrow adapter from the existing broker-normalized MarketDataRecord into the Batch 15 quote-quality observation. It maps only canonical contract identity, normalized exchange timestamp, local monotonic receive time, bid/ask, and top-of-book sizes.

Validation fixtures now exercise both existing normalizers with synthetic provider-native shapes:
- Angel One SmartAPI: scaled prices, exchange timestamp in milliseconds, token identity, and buy/sell depth arrays.
- Shoonya Noren: touchline fields (lp/bp1/sp1/bq1/sq1), exchange timestamp in seconds, and token/exchange identity.

The fixtures verify that different broker tokens and display symbols still resolve to the same NIFTY option contract, with matching normalized bid/ask values. They also cover strike mismatch, missing timestamp, malformed expiry, unsupported provider, and incomplete-depth rejection.

Safety boundary:
- This adapter is a pure read-only conversion function. It is not registered with either WebSocket, ingress queue, manager, scanner, persistence layer, or UI.
- Generation/authentication/current-connection status and local monotonic receive time must be supplied by the caller; no status is inferred from a normalized tick.
- Depth completeness defaults to false. A top-of-book record is not silently promoted to complete market depth; caller must provide separately verified evidence.
- No quote source is selected, no broker-side mixing occurs, and no orders can be produced by this module.
- Seven focused adapter tests are committed but have not been executed here. Exact-head CI and backend regression remain outstanding.


## 22. Batch 17 — instrument-wise quote parity metrics and bounded history

Added a pure diagnostic analyzer that computes, only for matching canonical contract keys:
- absolute and relative-basis-point bid difference;
- absolute and relative-basis-point ask difference;
- bid-size and ask-size absolute differences;
- midpoint absolute and relative-basis-point difference;
- exchange timestamp skew in nanoseconds.

On canonical identity mismatch every price/size/time parity metric is suppressed. For stale but structurally valid quotes, divergence metrics may remain visible for diagnostics while the quality state remains stale; this must never be interpreted as an executable quote.

Added a thread-safe bounded in-memory history keyed by canonical contract. Capacity is configurable per contract (default 300), snapshots are immutable tuples, out-of-order local observation times are rejected, and clear reports the number of retained observations removed.

Safety and limitations:
- The history is process-memory only: it is not durable across restarts and is not wired to a broker callback, persistence store, API, UI, scanner, or strategy.
- This batch measures quote parity; it does not decide which broker is better or eligible to execute.
- No cross-broker bid/ask mixing, source promotion, failover, or order routing.
- Seven focused parity/history test functions are committed but have not been executed here. Exact-head CI and full backend regression remain required.


## 23. Batch 18 — durable parity diagnostics and read-only API

Added `SQLiteQuoteParityStore` for derived provider-parity diagnostics only:
- Stores canonical contract key, observation UTC timestamp, provider labels, quality state, identity match, per-provider valid/fresh flags, and JSON metrics/reasons.
- Decimal metrics are serialized as decimal strings to avoid binary-float precision loss.
- Uses a dedicated SQLite table and indexes; WAL and a busy timeout are enabled for file-backed databases.
- Supports bounded reads (1–1000 rows) and explicit UTC-cutoff pruning.
- Reopen tests verify durability; mismatch tests verify that suppressed price metrics stay null.

Added read-only endpoint:
- `GET /api/v1/market-data/provider-quote-parity?contract_key=<canonical-json>&limit=100`
- Returns derived diagnostic rows only; raw bid/ask, credentials, and executable quote payloads are not exposed.
- Returns HTTP 503 while disabled.
- Opt-in settings: `PROVIDER_QUOTE_PARITY_ENABLED=false`, `PROVIDER_QUOTE_PARITY_DB=./provider_quote_parity.db`, `PROVIDER_QUOTE_PARITY_RETENTION_DAYS=7`.
- On startup, enabled store is opened and rows older than the configured UTC retention window are pruned; store is closed on application shutdown.

Important boundaries:
- Feature is disabled by default. No live producer currently writes parity observations into this store, so enabling it creates the table/API but does not itself create observations.
- No broker callback, scanner, Box Spread, IV Scanner, strategy, or order path is connected.
- SQLite store and API tests are committed but have not been executed in this session. Full backend tests and exact-head CI are still required.
- This is a diagnostics foundation, not production feed validation or broker failover approval.
