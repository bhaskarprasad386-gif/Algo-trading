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


## 24. Batch 19 — bounded shadow parity ingestion worker

Added `BoundedQuoteParityWorker` and immutable `QuoteParityEnvelope`:
- Non-blocking FIFO submission; all parity calculation and SQLite writes run on one worker thread, not in the submit/callback path.
- Queue capacity is configurable (`PROVIDER_QUOTE_PARITY_QUEUE_CAPACITY=2048` by default).
- Queue overflow latches an integrity fault and rejects further submissions; pending queued work is discarded rather than silently reported as complete.
- Both providers must have explicitly activated current generations. A generation change fences already-queued observations before persistence.
- Duplicate pair identity includes provider, generation and exchange timestamp; timestamp-less observations use their local receive timestamp.
- Out-of-order local receive timestamps are rejected per canonical contract/provider pair.
- Dedupe keys and per-contract ordering state are bounded; counters expose queue depth/capacity, duplicates, stale generations, out-of-order events, overflow, persistence, handler errors and shutdown discards.
- Graceful shutdown drains accepted items within a bounded timeout. Non-draining shutdown discards pending items. If shutdown times out, application lifecycle leaves the SQLite handle open rather than closing it under a possibly active worker.
- Added read-only `GET /api/v1/market-data/provider-quote-parity/status` for worker health.

The opt-in application lifespan now starts/stops the worker with the parity store. It remains disabled by default. No producer activates generations or submits live Angel/Shoonya quotes yet; the worker currently has no live broker integration.

Eleven focused worker tests cover persistence, explicit generation gating, duplicate and out-of-order rejection, overflow fail-closed, bounded dedupe state, canonical identity validation, graceful drain, queued generation fencing, and timestamp-less duplicate identity. Tests have been committed but not executed in this session; exact-head CI and full regression remain required.

No changes to Box Spread or IV Scanner strategy logic, WebSocket subscriptions, source selection, failover, or order routing.

## Batch 20 — bounded provider quote pairing boundary

Added `backend/app/market_data/provider_quote_pairer.py` and focused tests in
`backend/tests/test_provider_quote_pairer.py`.

- Accepts only normalized `ProviderQuoteObservation` values; it never reads
  broker SDK payloads, opens sockets, mutates subscriptions, or calls scanners.
- Requires explicit authenticated/current flags and an already activated
  provider generation. Generation advancement clears that provider's pending
  observations and is also forwarded to the parity worker.
- Holds at most two provider observations per bounded contract slot and bounds
  emitted pair identities separately. No unbounded per-tick history is retained.
- Emits only same-canonical-contract Angel One/Shoonya pairs. The worker remains
  responsible for its own generation fence, freshness evaluation, diagnostics,
  and durable write.
- A pair identity is marked emitted only after the worker accepts it. Worker
  rejection is counted and is not reported as successful submission.
- This is a pure pairing boundary, not live broker integration. Existing Angel
  normalized callbacks and Shoonya's current dispatch queue have not been wired
  to it. In particular, trustworthy socket/session generation and authentication
  metadata still need to be threaded into a dedicated read-only observer path.
- Five focused tests were added. They have not been executed in this environment.
- No scanner, order, broker source-selection, failover, or live subscription
  behavior was changed. Shadow mode remains default-off.

## Batch 21 — Shoonya authenticated normalized-observation hook

- Added an optional `on_tick_observation` callback to `ShoonyaWebSocket` and
  surfaced the same optional hook through `ShoonyaMarketDataSupervisor`.
- The hook receives the existing immutable `MarketDataRecord`, current
  `_feed_epoch`, explicit authenticated state, and local monotonic receive
  timestamp. It is dispatched on the existing market tick dispatcher thread,
  not the transport callback thread.
- Legacy `on_tick(dict)` payload and the separate order queue remain unchanged.
  No second market queue was added.
- The observation is rechecked against the current authenticated epoch immediately
  before observer delivery. Reconnect/epoch replacement fences queued observer
  callbacks; observer exceptions are isolated from the dispatcher.
- Added two focused regression tests for metadata delivery and retired-epoch
  rejection. Tests are committed but not executed here.
- This is only the Shoonya side of the metadata bridge. Angel's manager still
  needs an equally explicit trusted authentication/session-current signal before
  a parity producer may activate its generation. No provider pairer, parity
  worker, scanner, subscription, source selection, failover or order path is
  connected by this batch.


## Batch 22 — Angel normalized observer boundary (authentication fail-closed)

- Added additive register/unregister methods for normalized diagnostics observers
  on CommonWebSocketManager; existing raw and normalized callback signatures
  and registration paths are unchanged.
- Observer metadata includes the immutable normalized record, SocketGroup,
  manager socket generation, current-connection check, and monotonic receive
  timestamp captured at callback ingress.
- Critical limitation: the current SmartAPI adapter exposes transport-open and
  failure callbacks but no verified broker authentication acknowledgement in
  this manager contract. Therefore the observer always supplies
  authenticated=false. It is intentionally impossible for this hook alone
  to activate an Angel generation in the parity worker or pairer.
- Retired/unknown generations are rejected by the manager before routing; the
  observer rechecks that its socket generation remains current before delivery.
  Observer exceptions are isolated from existing scanner/consumer callbacks.
- Added three focused tests for metadata/fail-closed auth, retired-generation
  suppression, and observer exception isolation.
- No parity worker wiring, scanner change, Box/IV behavior change, subscription
  change, source selection, failover, or order route was added. Exact-head CI
  and tests remain unverified.

- Follow-up static review caught and corrected a potential fan-out regression:
  observers and legacy callbacks are now grouped by instrument without collapsing
  multiple consumers subscribed to the same contract. A dedicated regression
  test covers two legacy consumers plus an observer on one instrument.


## Batch 23 — complete Angel observer registration and ingress metadata

Completed the Angel observer boundary that was documented in Batch 22 but had
an incomplete implementation:
- Added the dedicated observer registry and public register/unregister methods.
- Captures the monotonic receive timestamp at accepted callback ingress and
  associates each normalized observer with the exact matched instrument.
- Counts observer-only consumers as valid callback routes and exposes them in
  the manager's consumer diagnostics.
- Removes observer registrations when a consumer is cleared.
- Preserves the legacy normalized callback API and all existing callback fan-out.
- Angel authentication remains explicitly false because the current SmartAPI
  manager has no separately verified broker-auth acknowledgement. The observer
  cannot activate the Angel parity generation; this is an intentional fail-closed
  boundary, not a live readiness claim.
- The existing focused telemetry tests exercise metadata, stale generation,
  observer exception isolation, and multiple legacy consumers on one instrument.
  They remain unexecuted in this session; exact-head CI is required.
- No scanner, Box Spread, IV Scanner, broker subscription, source selection,
  failover, or order-routing behavior changed.


## Batch 24 — explicit Angel observer authentication readiness diagnostics

- Added a read-only `normalized_observer_authentication` diagnostic to the common Angel feed snapshot.
- State is `NOT_VERIFIABLE`; `authenticated` and parity-generation activation remain false until a separately verified broker-authentication acknowledgement exists.
- The diagnostic explicitly identifies the missing evidence rather than allowing transport-open/connected state to be mistaken for authenticated market data.
- Added a focused assertion that the diagnostic remains fail-closed.
- No SmartAPI lifecycle, subscription, scanner, Box Spread, IV Scanner, source-selection, failover, or order-routing behavior changed.
- Tests are committed but have not been executed in this session; exact-head CI remains required.


## Batch 25 — distinguish Angel transport readiness from authentication evidence

- Reviewed the upstream `angel-one/smartapi-python` `SmartWebSocketV2` implementation: `_on_open` delegates to the application open callback; its control-message handler handles ping/pong; the documented sample subscribes from `on_open`. These are transport/control/subscription lifecycle events, not a separately exposed broker-authentication acknowledgement.
- Extended the common-feed diagnostic to state explicitly that transport-open and subscription-send are not authentication evidence, and that no authentication acknowledgement has been observed.
- Kept `authenticated=false` and parity-generation activation disabled.
- Extended the regression assertion for these explicit fail-closed fields.
- No SDK monkey-patching, scanner, Box Spread, IV Scanner, subscription, source-selection, failover, or order-routing changes.
- Tests remain unexecuted in this session; exact-head CI is required.


## Batch 26 — shared auditable session and feed-readiness evidence contract

This batch defines the common evidence vocabulary for the two broker observers. It is an additive architecture contract; it does not wire either observer into the parity pairer or worker.

### Required evidence dimensions

Every provider session snapshot must keep these dimensions independent:

- **Transport:** socket lifecycle only (CLOSED, OPENING, OPEN, CLOSING, ERROR). Transport-open proves only that a WebSocket transport opened.
- **Authentication:** UNKNOWN, AUTHENTICATED, REJECTED, TIMED_OUT, NOT_VERIFIABLE, plus evidence kind, event time, and connection generation. A boolean without evidence provenance is insufficient.
- **Subscription:** desired, request-sent, broker-acknowledged (only where the protocol actually acknowledges it), and active instrument/token coverage. Sending a request is not proof that the broker accepted it.
- **Feed liveness:** last raw frame and last valid normalized market event, each with wall-clock and monotonic receive timestamps and age. Heartbeats count as transport liveness, never as market-data freshness.
- **Integrity:** current generation, stale-generation rejects, malformed-frame rejects, queue depth/capacity, overflow latch, callback/worker errors, and last integrity fault.
- **Quote quality:** canonical identity, exchange timestamp validity/age, receive age, bid/ask validity, crossed-market status, and depth completeness.

### Provider evidence mapping

- **Shoonya:** its protocol authentication acknowledgement (t=ck, s=OK) is the authentication evidence. It is scoped to the active feed epoch. Authentication rejection, missing-ack timeout, or epoch replacement invalidates the authenticated state. A heartbeat or market tick cannot manufacture an authentication acknowledgement.
- **Angel One SmartWebSocketV2:** current reviewed SDK callbacks establish transport-open and control/market traffic, but the manager has no separately verified broker-authentication acknowledgement. Its state therefore remains NOT_VERIFIABLE / authenticated=false; do not infer authentication from on_open, ping/pong, subscription send, or receipt of a tick.

### Derived readiness rules

- TRANSPORT_OPEN is a transport fact, not an authenticated session.
- AUTHENTICATED requires provider-specific positive acknowledgement tied to the current generation/epoch; stale or unproven evidence fails closed.
- SUBSCRIPTION_READY requires protocol-level acknowledgement where the protocol defines one, plus validated desired-token coverage. If no acknowledgement exists, report that limitation rather than silently upgrading request-sent to active.
- FEED_READY additionally requires a fresh valid normalized market event, valid canonical identity/timestamps, and no latched integrity fault. Heartbeats alone do not satisfy it.
- PARITY_ELIGIBLE requires both providers independently authenticated, current-generation, feed-ready, identity-compatible observations. A provider that is NOT_VERIFIABLE cannot activate its parity generation.
- ORDER_ELIGIBLE is out of scope and must never be inferred from any of these market-data readiness states.

Snapshots must expose raw evidence and derived state separately so operators can audit why a provider is or is not ready. Missing evidence is unknown/not-verifiable, never success. Generation changes clear all generation-scoped authentication, subscription acknowledgements, freshness, and integrity state before a replacement session can become ready.

### Scope and validation boundary

- This is a contract/documentation batch only; no runtime readiness aggregator, broker callback wiring, pairer activation, source selection, failover, scanner/UI integration, or order routing is introduced.
- Existing Box Spread and IV Scanner paths and subscriptions remain unchanged.
- The implementation follow-up must add pure state-transition tests for acknowledgement, rejection, timeout, stale generation, heartbeat-only liveness, subscription-send-only, stale market data, and queue-integrity fault before any runtime integration.
- Tests, exact-head CI, live broker validation, merge, and deployment are not claimed by this documentation-only change.


## Batch 27 — fail-closed provider readiness state machine

- Added an isolated provider readiness reducer for Angel One and Shoonya. It is not wired to broker callbacks, the quote pairer/worker, scanner fan-out, source selection, or order routing.
- Tracks transport, authentication evidence and generation, subscription request/acknowledgement coverage, heartbeat liveness, valid fresh market events, and sticky integrity faults independently.
- Shoonya can become authenticated only from an explicit current-generation `shoonya_ak_ack` evidence event while transport is open. Rejection and timeout remain non-authenticated.
- Angel One remains `NOT_VERIFIABLE`; transport-open, arbitrary acknowledgement-shaped input, and market ticks cannot authenticate it.
- Subscription request-sent is not acknowledgement. If the protocol acknowledgement capability is unavailable, subscription state is `NOT_VERIFIABLE`, never ready.
- Feed readiness requires current transport, authenticated session, full desired-token acknowledgement, fresh valid market event, valid canonical identity/timestamp, and no integrity fault. Heartbeats alone do not count.
- Generation replacement clears all generation-scoped evidence. Transport close invalidates auth/freshness and subscription acknowledgement without incorrectly latching an integrity fault.
- Order eligibility is always false; parity eligibility is derived from feed readiness only. No scanner, Box Spread, IV Scanner, subscriptions, live broker sessions, or order route were changed.
- Added focused tests for positive/negative auth acknowledgement, timeout, Angel fail-closed behavior, heartbeat-only liveness, subscription-send-only, unsupported subscription acknowledgement, stale/invalid market data, generation fencing/reset, integrity fault, transport close, and strict evidence types.
- Tests have been added but not executed in this environment. Exact-head Actions and full regression remain required before runtime integration.

- Follow-up hardening accumulates partial per-token subscription acknowledgements until the complete desired set is covered; transport close clears generation-scoped acknowledgement and market-validity evidence without converting a normal close into an integrity fault. Regression cases cover partial coverage, close reset, and strict token input types.
- Readiness freshness is re-evaluated against monotonic receive age at snapshot time (5-second default), so a previously fresh tick cannot keep FEED_READY true indefinitely. Authentication timeout/rejection is terminal for that generation; late success cannot revive the same epoch. Added deterministic regressions for both cases.


## Batch 28 — live callback evidence integration review (fail-closed gate)

A source-level integration review was performed against the current branch implementations of
`ShoonyaWebSocket`, `ShoonyaMarketDataSupervisor`, `CommonWebSocketManager`,
and the application lifespan wiring.

### Confirmed callback evidence

- Shoonya's `ak` response is processed once per active feed epoch. A positive
  `s=OK` response sets the adapter's authenticated state; rejection closes the
  socket. The existing optional normalized observation hook is delivered by the
  existing market dispatch worker with the feed epoch, authenticated flag, and
  monotonic receive timestamp. This is suitable raw evidence for a later
  readiness bridge; it is not yet connected to `ProviderReadiness`.
- Shoonya's supervisor currently passes through the optional observation hook,
  but the application does not register one. The supervisor's state callback
  currently exposes state/reason only, while the adapter snapshot contains the
  epoch and auth state.
- Shoonya subscription code sends `t=t` and `t=u` frames and tracks the
  locally desired descriptor set. The inspected implementation does not expose
  a distinct per-token broker subscription acknowledgement. Therefore the
  readiness reducer must use subscription capability=false and report
  `NOT_VERIFIABLE`; local registration or successful socket send cannot be
  upgraded to acknowledged coverage.
- Angel's normalized observer exposes SocketGroup, manager generation,
  connection-current flag, and monotonic receive timestamp. Its documented
  authentication field is intentionally always false because the current
  SmartAPI manager has no verified authentication acknowledgement callback.

### Integration decision

No live readiness bridge was wired in this batch. The evidence contract cannot
produce `FEED_READY` or `PARITY_ELIGIBLE` from the currently verified sources:
Shoonya lacks independently verified subscription ACK coverage, and Angel lacks
verified authentication ACK evidence. Wiring the reducer while fabricating either
signal would create misleading operational health and could allow untrusted
observations into parity analysis.

### Safe next implementation gate

1. Add a read-only session-evidence observer that forwards actual Shoonya state,
   epoch, auth ACK/rejection/timeout, desired subscription changes, and normalized
   market observations into the reducer. Keep subscription state
   `NOT_VERIFIABLE` unless an official protocol ACK is established.
2. Feed Angel transport/generation/normalized-event evidence into its reducer,
   with authentication permanently `NOT_VERIFIABLE` until a documented,
   independently verified SDK/broker acknowledgement exists.
3. Expose per-provider raw evidence and derived readiness in diagnostics only.
4. Keep the pairer/worker disconnected while either provider is ineligible.
5. Add deterministic reconnect, close, late-callback, stale-tick, and existing
   scanner callback compatibility tests before considering any opt-in integration.

No scanner, Box Spread, IV Scanner, subscription universe, order routing, or
production runtime behavior was changed. No tests were executed or claimed as
passing; exact-head CI, official protocol confirmation, live broker validation,
merge, and deployment remain outstanding.


## Batch 29 — read-only broker callback-to-readiness bridge

- Added `ProviderReadinessBridge`, which maintains one Shoonya readiness reducer
  and independent Angel One reducers per socket group/generation. Per-group
  isolation avoids allowing one Angel shard's traffic to mask another shard.
- Added an optional Shoonya session-evidence callback. It reports adapter state,
  feed epoch, connected/authenticated flags, and monotonic observation time;
  callback exceptions are isolated from the existing state callback.
- Wired the Shoonya supervisor's existing normalized tick observation hook and
  the additive session-evidence hook to the bridge. Only the adapter's
  `LIVE` state with its authenticated flag (set after the Noren `ak/s=OK`
  response) can record the Shoonya auth evidence.
- Registered an additive Angel normalized observer on the existing shared
  manager from the runtime-health endpoint. It does not add or remove broker
  subscriptions and does not replace Box Spread or IV Scanner callbacks.
- Angel transport/generation/market observations are captured per socket group,
  but authentication and subscription remain `NOT_VERIFIABLE`. The callback's
  auth-shaped metadata is deliberately not trusted to promote authentication.
- Runtime health now exposes `provider_readiness` alongside existing feed and
  scanner diagnostics. The bridge is read-only: pairing, scanner fan-out, and
  order eligibility are explicitly false.
- Shoonya subscription ACK capability remains false because no independent
  per-token broker ACK was verified. Thus even after a valid auth ACK and fresh
  market event, Shoonya `feed_ready` and `parity_eligible` remain false.
- Added focused bridge tests for Shoonya epoch/auth evidence, reconnect reset,
  Angel fail-closed authentication, per-shard generation isolation, and the
  no-activation boundary.
- Tests have been committed but not executed in this environment. Exact-head
  CI, callback compatibility regression, live broker validation, merge, and
  deployment are not claimed. Existing scanner subscriptions and order paths
  were not modified.


## Batch 30 — callback ordering and legacy compatibility hardening

- The readiness bridge now rejects out-of-order session and market observations
  using per-generation monotonic receive ordering. Older same-generation events
  cannot roll back a newer Shoonya authentication state or replace a newer quote
  observation. Angel ordering is isolated by socket group and generation.
- Added bounded scalar rejection counters to readiness diagnostics; no callback
  payload history is retained.
- Corrected the Shoonya rejection transition so an `AUTH_FAILED` event records
  the rejection evidence and closes transport readiness while preserving the
  `REJECTED` authentication result.
- Angel transport evidence is now explicitly labelled as a current-generation
  normalized market callback only; the current observer does not receive the
  manager's socket-open/socket-close lifecycle, so it must not be represented
  as full lifecycle telemetry.
- Runtime health registers the Angel observer once per shared manager instance
  rather than re-registering it on every health request. It remains additive
  and does not mutate subscriptions or scanner callbacks.
- Added regressions for out-of-order Shoonya state/tick events, auth rejection
  close behavior, out-of-order Angel group observations, and non-current
  connection callbacks.
- Added Shoonya adapter/supervisor tests proving the new session observer is
  additive: legacy state callback still runs, either observer's exception is
  isolated, and the supervisor passes the callback without replacing existing
  callbacks.
- Existing common-feed regression coverage already verifies Angel observer
  delivery, retired-generation suppression, observer exception isolation,
  preservation of multiple legacy consumers on one instrument, observer-only
  routing, and unregister behavior. These tests were reviewed but not executed
  in this session.
- No Box Spread or IV Scanner callback, subscription, scanner calculation,
  source selection, pairer, or order path was changed. Tests are committed but
  not run here; exact-head CI, full regression, live broker validation, merge,
  and deployment remain outstanding.


## Batch 31 — verified-session gating and timestamp integrity

- Shoonya `LIVE` is now accepted as authentication evidence only when the
  adapter reports both `connected=true` and `authenticated=true` for the
  current feed epoch. A contradictory LIVE callback cannot authenticate a
  closed transport.
- Shoonya quote evidence is now accepted only after the bridge's own reducer
  confirms the same current generation is OPEN and AUTHENTICATED. A callback's
  boolean alone cannot create market-event evidence before the Noren auth ACK,
  or after a rejection/close.
- Added tests for contradictory LIVE state, unauthenticated quote callbacks,
  quotes after auth rejection, future exchange timestamps, and missing
  canonical symbol identity.
- Exchange timestamp freshness remains fail-closed: valid identity and a
  positive timestamp are insufficient if the source timestamp is outside the
  five-second wall-clock tolerance. Heartbeats remain excluded as market data.
- No Angel authentication ACK or Shoonya per-token subscription ACK was
  introduced. Neither provider can become parity eligible or order eligible
  from these changes.
- No Box Spread / IV Scanner subscription, calculation, consumer callback,
  pairer, strategy fan-out, persistence, or order routing was modified.
- Tests have been added but not executed in this session. Exact-head CI,
  full regression, broker validation, PR merge, and deployment remain
  outstanding.


## Batch 32 — Angel One socket lifecycle evidence

- Added an optional lifecycle observer to `MarketDataWebSocket`. The real
  SmartAPI wrapper reports OPEN, ERROR, and CLOSED with monotonic receive time;
  observer exceptions are isolated from broker callbacks and scanner delivery.
- `CommonWebSocketManager` now exposes an additive transport-observer registry
  and passes a generation-bound lifecycle callback only to its managed
  `MarketDataWebSocket` adapter. Existing raw/normalized scanner callbacks,
  subscriptions, and reconnect ownership remain unchanged.
- Lifecycle notifications are checked against the current socket generation.
  A CLOSED event is also accepted for the matching retiring generation after
  the manager has detached the socket, but a late close from an older
  generation cannot close a replacement.
- Connect/handshake exceptions are surfaced as ERROR evidence even if the SDK
  throws before delivering its normal error/close callback.
- Runtime health installs both normalized-tick and transport-lifecycle
  observers. Angel diagnostics can now distinguish OPEN transport from CLOSED
  transport, while authentication remains NOT_VERIFIABLE and subscription
  acknowledgement remains NOT_VERIFIABLE.
- The readiness bridge rejects late Angel market callbacks after a generation
  is closed; a late tick cannot reopen the transport state.
- Added tests for adapter lifecycle callbacks and exception isolation,
  manager observer fan-out/retirement semantics, and bridge lifecycle ordering,
  generation fencing, close invalidation, and late-tick rejection.
- No Box Spread or IV Scanner behavior, source selection, subscriptions,
  calculations, pairer, persistence, or order routing was changed.
- Tests are committed but have not been executed in this session. Exact-head
  CI, full regression, real broker validation, merge, and deployment remain
  outstanding.


## Batch 33 — terminal Angel lifecycle failures and reconnect recovery

- Hardened the per-group Angel readiness bridge so an `ERROR` or `CLOSED`
  event is terminal for that socket generation. A delayed `OPEN` callback
  cannot resurrect the failed generation; only a higher manager generation
  can establish a new OPEN transport.
- Preserved valid recovery: a replacement generation begins independently in
  OPENING and may transition to OPEN. Authentication remains
  `NOT_VERIFIABLE`; transport recovery does not imply broker authentication.
- Updated the runtime evidence label to reflect that Angel socket lifecycle
  callbacks are now observed alongside normalized market callbacks.
- Added regression coverage for OPEN → ERROR → delayed OPEN rejection and
  successful OPEN on a replacement generation.
- Reviewed callback ordering: wrapper emits OPEN after connected state is set;
  ERROR after connected state is cleared and before the legacy failure callback;
  CLOSED after connected state is cleared. The manager binds each observer to
  the socket generation, accepts a matching retiring-generation CLOSED event,
  rejects stale older-generation events, and emits ERROR on immediate connect
  exceptions. Lifecycle observer failures remain isolated.
- No Box Spread or IV Scanner subscriptions, callbacks, calculations, scanner
  source selection, pairing, persistence, or order routing changed.
- Tests remain committed but unexecuted in this session. Exact-head CI,
  full regression, broker validation, PR merge, and deployment remain
  outstanding.


## Batch 34 — Shoonya retired-socket close race

- Found a narrow reconnect race in the Shoonya adapter: a WebSocket callback
  could pass the epoch/socket guard, pause before entering `_on_close`, and
  then resume after a replacement socket had become current. Unlike
  `_on_error`, `_on_close` did not recheck socket identity while holding the
  adapter lock, so the retired socket could clear the replacement session's
  connected/authenticated flags.
- Added an atomic active-socket identity check inside `_on_close`. A close
  from a retired socket is now ignored before it can mutate current session
  state or emit a disconnect observation.
- Added a regression test that simulates a retired socket close after a
  replacement session is authenticated, and asserts the replacement remains
  connected/authenticated with no state callback.
- Updated the existing direct timeout-close test to attach its fake socket as
  the active socket, matching the real callback ownership contract.
- Existing lifecycle remains unchanged for the current socket: auth rejection,
  watchdog timeout, normal disconnect, and explicit stop still flow through
  their established state transitions.
- No Angel One, Box Spread, IV Scanner, subscription universe, scanner
  calculation, provider selection, pairing, persistence, or order routing
  behavior changed.
- Tests remain committed but unexecuted in this session. Exact-head CI,
  full regression, broker validation, PR merge, and deployment remain
  outstanding.


## Batch 35 — Shoonya terminal authentication epoch fencing

- Reviewed the Shoonya session-observation bridge against adapter callback
  ordering, reconnect epoch replacement, authentication watchdog timeout,
  explicit stop, and delayed callback delivery.
- Found that a delayed `LIVE` / connected callback with a newer monotonic
  observation time could reopen the transport state after `AUTH_FAILED` or
  `AUTH_TIMEOUT`. The authentication reducer itself remained fail-closed
  (REJECTED/TIMED_OUT), but the transport diagnostic could misleadingly return
  to OPEN within the same epoch.
- The bridge now treats a CLOSED Shoonya epoch as terminal for subsequent
  `AUTHENTICATING`, `LIVE`, or connected callbacks. It rejects those
  observations and increments stale-observation diagnostics. A higher feed
  epoch still begins with fresh state and can recover normally.
- Added regression coverage for delayed LIVE after both AUTH_FAILED and
  AUTH_TIMEOUT. The tests assert transport remains CLOSED, authentication
  remains REJECTED/TIMED_OUT, and the late observation is counted as stale.
- Existing monotonic ordering guard, adapter socket identity guard, Angel
  generation fencing, and shadow-only boundary remain intact.
- No scanner subscriptions, calculations, callbacks, provider selection,
  pairing, persistence, or order routing changed.
- Tests remain committed but unexecuted in this session. Exact-head CI,
  full regression, broker validation, PR merge, and deployment remain
  outstanding.


## Batch 36 — Shoonya partial worker-start rollback

- Audited the Shoonya adapter's start/stop/restart path and supervisor rollback
  behavior. The supervisor already attempted cleanup when adapter startup
  raised, but a direct adapter caller could still leave earlier dispatcher or
  heartbeat workers running if a later Thread.start() failed.
- Made adapter startup transactional: workers are started in a tracked order;
  on any startup exception, the stop event is set and already-started workers
  are joined against one shared three-second cleanup budget.
- If cleanup completes, worker references are cleared so a later explicit
  start can create one fresh worker generation. If a worker remains alive
  after the bounded wait, its reference and name are retained so duplicate
  workers cannot be started over it.
- Added a deterministic regression test that fails the third worker start and
  verifies the first two workers are stopped and all worker references are
  cleared.
- The change is isolated to Shoonya worker lifecycle. Angel One manager,
  Box Spread and IV Scanner callbacks/subscriptions/calculations, broker
  selection, parity pairing, persistence, and order routing are unchanged.
- Tests are committed but have not been executed in this session. Exact-head
  CI, full regression, live broker validation, merge, and deployment remain
  outstanding.


## Batch 37 — tracked Angel socket retirement during manager shutdown

- Jointly audited CommonWebSocketManager shutdown, its bounded socket-close
  helper, the Angel wrapper's SDK close/transport-thread cleanup, and the
  optional per-shard callback worker.
- Found a shutdown observability gap: manager.close() used the bounded close
  helper directly. If an SDK close call outlived the wait, the daemon close
  worker could remain alive without being entered in retired-transport
  diagnostics or supervised until completion.
- Manager shutdown now routes every captured (SocketGroup, socket) pair
  through the tracked retirement path. A close worker that exceeds the
  bounded wait remains visible with its group, reason and generation metadata;
  its watcher clears the retirement record only after the close call exits.
- The shutdown path captures group/socket pairs before clearing active maps,
  preserving the correct shard identity in diagnostics. It continues to
  release manager locks before waiting for callbacks or socket cleanup.
- Added tests proving manager shutdown uses tracked retirement with the
  original SocketGroup, and that a deliberately blocked close remains visible
  until its worker exits.
- No scanner callbacks, subscriptions, calculations, provider selection,
  parity pairing, persistence, or order routing changed. Angel async callback
  dispatch remains opt-in and default-off.
- Tests are committed but have not been executed in this session. Exact-head
  CI, full regression, live broker validation, merge, and deployment remain
  outstanding.



## Batch 38 — Angel One late connect completion fencing

- Audited the Angel One wrapper's connect/close overlap. A connect attempt
  previously assigned the SDK socket outside the lifecycle lock and, after
  its blocking handshake returned, unconditionally copied transport-thread
  state from the wrapper's mutable `self.websocket` reference. If close or a
  replacement attempt had already detached that socket, the late completion
  could lose ownership of the retired transport thread or publish stale
  connection bookkeeping.
- Each connect attempt now retains its own local SDK socket reference. Socket
  publication is guarded by the lifecycle lock and refuses to publish after
  stop. On handshake completion or exception, the attempt rechecks both the
  stop flag and socket identity before mutating shared state.
- A superseded attempt is cleaned up against its own SDK socket and returns
  without changing the replacement wrapper state. Exception accounting and
  retry behavior are likewise suppressed for stale attempts.
- Socket callbacks are also identity-fenced: a retired socket's late OPEN,
  ERROR, or market-data callback cannot mutate the current wrapper state or
  reach the existing data consumer. A retired CLOSED event may still report
  lifecycle closure for its own manager generation, but cannot mark a newer
  wrapper session disconnected or trigger replacement recovery.
- Added a deterministic blocking-connect regression: close the wrapper while
  the SDK connect call is paused, then release the old attempt and assert it
  cannot republish a socket or connected state.
- This change does not switch Angel authentication from
  NOT_VERIFIABLE, alter manager generation ownership, add subscriptions, or
  change Box Spread / IV Scanner callbacks, calculations, or source selection.
- The focused test is committed but has not been executed in this session.
  Exact-head CI, full regression, live broker validation, PR merge, and
  deployment remain outstanding.



## Batch 39 — manager shutdown versus late connect failure

- Reviewed the manager's asynchronous connect worker across broker admission
  wait, handshake success/failure, generation replacement, and shutdown.
  Successful late handshakes were already fenced and retired against their
  local socket; failed handshakes, however, still incremented global
  `connect_failures` and overwrote `last_connect_failure` even after that
  socket's generation had been retired or the manager had closed.
- The connect exception path now treats a failure as current only when the
  manager is not closed and both socket identity and generation still match.
  Only a current failure can invalidate a shard, update failure telemetry,
  record a runtime diagnostic, or initiate recovery. A late exception from a
  retired worker is ignored for current manager health and cannot overwrite a
  replacement generation's failure details.
- Added a deterministic regression that blocks a connect worker, closes the
  manager, then releases the worker to raise. It verifies the late failure
  cannot republish a socket or pollute connect-failure health.
- Existing safeguards remain: manager close clears active socket registries
  under reconciliation serialization; the connect worker rechecks current
  identity before and after broker admission; late successful sockets are
  retired before global admission is released; pending-generation cleanup is
  conditional on matching generation.
- No Box Spread or IV Scanner subscriptions, callbacks, calculations, feed
  selection, pairing, persistence, or order routing changed.
- The regression is committed but has not been executed in this session.
  Exact-head CI, full regression, live broker validation, PR merge, and
  deployment remain outstanding.


### Batch 40 — process-wide broker connect admission regression

- Audited the timeout/recovery path for a blocked Angel handshake. A manager can retire and replace its local socket generation, but the replacement must not open a second broker handshake while the old connect() call is unresolved.
- The process-wide _SHARED_BROKER_CONNECT_ADMISSION_LOCK already enforces this boundary across manager instances, not merely within one scanner or manager. The new regression test exercises two independent CommonWebSocketManager instances: the second socket is created but its broker connect() is held until the first handshake returns.
- This is deliberately conservative: a hung SDK handshake can delay new broker sessions rather than risk overlapping sessions and consuming the broker connection limit. The test does not claim the underlying SDK call can be forcibly cancelled.
- Added test_shared_broker_admission_serializes_connects_across_manager_instances.
- Tests committed but not executed in this environment. No scanner feed, subscription, runner, pairing, or order behavior changed.


### Batch 41 — retirement follows the actual Angel transport thread

- Confirmed a lifecycle gap: `MarketDataWebSocket.close()` can return after its bounded abort attempt while the SDK `run_forever` transport thread remains alive and reports `transport_close_timed_out`. The manager previously considered retirement complete when its wrapper `close()` worker exited, which could release the replacement gate too early.
- Updated `CommonWebSocketManager._close_socket_bounded`: after invoking `socket.close()`, the tracked close worker now also waits for the socket's `_transport_thread` to terminate. The existing retirement watcher therefore keeps the broker slot occupied and blocks replacement admission until the actual transport exits. Alternate wrappers exposing only `health.transport_thread_alive` are handled conservatively when no thread handle exists.
- Added `test_retired_slot_waits_for_actual_transport_thread_after_close_returns`, which simulates a wrapper close returning while the transport thread remains alive and verifies no replacement socket is opened until that thread exits.
- Existing bounded SDK close/abort behavior remains unchanged. This does not claim Python can forcibly kill a stuck thread; it makes the unresolved transport visible and prevents an unsafe overlapping replacement.
- Tests committed but not executed in this environment. No scanner, strategy, subscription intent, pairing, or order-routing behavior changed.


### Batch 42 — Shoonya shutdown must not wait behind socket writes

- Lifecycle audit found four outbound paths (`_on_open`, heartbeat, market subscription replay, and order subscription) held the adapter lifecycle lock while calling `ws.send()`. A blocked SDK/network write could therefore prevent `stop()` from acquiring the same lock, so shutdown could not even reach `ws.close()` to unblock the write.
- Moved socket writes outside the lifecycle lock. Each path now validates/snapshots current socket ownership and auth state under the lock, then performs network I/O after releasing it. Subscription ordering remains serialized by the dedicated `_subscription_send_lock`; no new lock is held by shutdown.
- Added parameterized regression coverage `test_blocked_shoonya_socket_write_does_not_hold_lifecycle_lock` for all four paths. It blocks `send()`, invokes `stop()`, and requires `close()` to be called promptly and both worker threads to finish.
- This removes lifecycle-lock starvation; it does not claim a blocked network call is forcibly killed. Existing bounded shutdown diagnostics still report any worker that fails to terminate.
- Tests committed but not executed in this environment. Shoonya remains opt-in/read-only; Angel/Box/IV scanner behavior and order routing are unchanged.


### Batch 43 — Angel reconnect worker lifecycle-generation fence

- Found a shutdown race in `MarketDataWebSocket`: `_reconnect_after_disconnect()` could pass its stopping check, pause before `connect()`, then `close()` could set `_stopping=True`; the delayed reconnect subsequently entered `connect()`, which unconditionally reset `_stopping=False` and could create a new socket after shutdown.
- Added a monotonically increasing `_lifecycle_generation`. `close()` increments it. Internal reconnect workers capture the generation and pass it to `connect()`; the generation/stopping check now occurs before any subscription or callback state mutation. A stale internal reconnect returns without reopening the wrapper.
- Explicit caller-initiated `connect()` remains the supported way to intentionally restart a previously closed wrapper; it advances the lifecycle generation, clears `_stopping`, and supersedes any older in-flight reconnect worker.
- Added tests for close winning the reconnect race, stale reconnect rejection without subscription mutation, and explicit manual restart after close.
- No scanner feed selection, subscription intent, provider readiness, strategy, or order-routing behavior changed. Tests committed but not executed in this environment.


### Batch 44 — simultaneous broker failure/recovery isolation

- Added bridge-level regression coverage for simultaneous Shoonya authentication rejection and Angel transport failure, followed by independent recovery into new generations.
- The test asserts Angel recovery cannot reopen or authenticate the rejected Shoonya epoch; Shoonya recovery cannot alter Angel's replacement generation; delayed failure callbacks from retired generations cannot close the replacements.
- Added a second test proving an Angel socket-group failure closes only that group and leaves another active group unchanged.
- This verifies the readiness evidence boundary, not live broker reconnection or provider parity. Angel authentication and subscription acknowledgement remain `NOT_VERIFIABLE`; Shoonya subscription acknowledgement remains `NOT_VERIFIABLE`; scanner fanout, pairing and order eligibility remain disabled.
- Tests committed but not executed. No production scanner, subscription, or reconnect policy was changed in this batch.



### Batch 45 — Shoonya late factory completion and worker retirement

- Found a supervisor lifecycle race: a blocking Shoonya WebSocket factory could return after `stop()` set the stop event; `_run_loop` previously published that socket and invoked `run_forever` without rechecking shutdown or the feed epoch.
- Added a post-factory stop/epoch check under the adapter lifecycle lock. A socket created for a stopped or superseded epoch is closed without publishing it or running its transport loop.
- Added a delayed-factory regression test: hold the factory during shutdown, release it after stop, and assert the socket was closed, `run_forever` never ran, and the supervisor exited.
- Reviewed existing resource safeguards: bounded tick/order queues, shared shutdown join deadline, worker-alive diagnostics, and Angel tracked retirement watchers. No new queue capacity, broker cooldown, or reconnect-policy changes in this batch.
- The new test was committed but not executed; repeated-cycle thread/memory profiling and live dual-broker recovery remain unverified. No scanner, pairing, or order-routing changes.


### Batch 46 — stale queued tick fence across Shoonya reconnect epochs

- Found that the tick dispatcher checked the queued tick epoch only before the shadow readiness observer; the legacy on_tick callback ran first without an epoch check. A tick queued by a retired socket could therefore reach a legacy consumer after reconnect.
- The dispatcher now checks epoch equality and current authenticated state before either callback. Stale items are discarded and counted as stale_dispatch_drops; valid current-epoch items retain the existing callback order and delivery behavior.
- Added regression coverage for seven queued retired epochs being drained after an eighth epoch becomes current, and for a ten-epoch burst respecting a deliberately small queue capacity (3) while recording overflow drops.
- Production queue limits and reconnect policy are unchanged. Tests committed but not executed. This is deterministic queue/epoch stress coverage, not a live-market throughput or memory benchmark.


### Batch 47 — Angel shard callback retirement fence

- Audited the opt-in Angel per-shard callback queue. Queued batches were generation-checked when dequeued, but a multi-invocation batch could continue invoking later consumers if retirement happened while an earlier callback was executing. Also, stop() did not invalidate the adapter generation.
- Adapter stop now advances its generation. The worker rechecks both closed state and batch generation immediately before every callback invocation, so retirement fences remaining invocations in an already-dequeued batch. A callback already executing cannot be cancelled safely and is allowed to finish.
- Added a deterministic regression test with a blocked first callback, concurrent non-draining retirement, and a second callback that must never execute.
- Existing Angel callback dispatch remains opt-in; no scanner subscriptions, callback dispatch mode, or broker routing behavior was changed. Test committed but not executed. This is a deterministic lifecycle race test, not a throughput or live-market test.


### Batch 48 — Angel callback worker retirement observability

- The manager already retired async per-shard callback adapters without joining them under its lifecycle lock. However, if a consumer callback blocked beyond the one-second stop budget, the adapter was removed from the active map and only a warning remained; health snapshots did not expose the still-running worker.
- Added manager-owned `retired_callback_workers` lifecycle metadata. A worker that outlives the bounded stop is now visible with shard, generation, thread name/identity, alive state, and retirement time. A dedicated watcher removes the record only after the worker exits; no callback payload or credential data is retained.
- Added a regression test using a deliberately blocked worker to verify it remains visible after timeout and is removed after exit.
- This affects diagnostics and cleanup visibility only. Async callback dispatch remains opt-in; no scanner, subscription, routing, reconnect, or broker selection behavior changed. Test committed but not executed.


### Batch 49 — Shoonya order and in-flight observer epoch fencing

- Joint worker audit found two Shoonya delivery gaps: order updates entered a separate queue without their source feed epoch, and a tick's shadow observer could still run after the legacy tick callback blocked while a reconnect replaced the feed epoch.
- Order queue entries now carry the current feed epoch and the order dispatcher validates epoch plus authenticated/non-stopping session immediately before invoking the consumer. Retired queued order updates are dropped and counted in the existing stale-dispatch counter.
- Tick dispatcher revalidates the feed epoch/session after the legacy callback returns and immediately before the shadow observer. An already-running legacy callback cannot be cancelled safely, but it cannot cause a retired quote to be forwarded to the later observer.
- Added deterministic tests for stale queued order updates and reconnect during a blocked legacy callback.
- No broker socket protocol, scanner feed, subscription set, or execution path changed. Tests committed but not executed.


### Batch 50 — Shoonya shutdown timeout recovery visibility

- The combined shutdown audit found that Shoonya recorded worker names when the shared three-second stop budget expired, but did not refresh that list if a blocked callback/worker exited later. The snapshot could therefore continue reporting a worker as alive after it had terminated.
- Added a single deduplicated shutdown watcher. It waits for the timed-out worker set, then recomputes actual liveness from the adapter's current worker references and clears the socket reference once the supervisor has exited. Snapshot now exposes whether the watcher itself is alive.
- Added a deterministic worker-release test confirming timeout diagnostics remain visible while blocked and clear after worker termination.
- This is shutdown observability/cleanup only; no socket reconnect, feed routing, subscription, scanner, or order behavior changed. Test committed but not executed.


### Batch 51 — Shoonya inbound socket-origin epoch binding

- A deeper callback-entry race remained: `_guard_callback` can validate a socket and then lose the lock before `_on_message` begins. If reconnect replaces the socket in that interval, an old market/order frame could otherwise be associated with the replacement's current epoch.
- `_on_message` now independently validates socket ownership and stop state while capturing the source epoch before updating frame freshness. It rechecks ownership/authentication/epoch before dispatch. Tick normalization receives the captured source epoch; order queue entries carry that same source epoch instead of sampling the current epoch later.
- Added parameterized regression coverage for stale old-socket order and tick frames reaching `_on_message` after replacement; they must not update frame freshness or either queue.
- No broker protocol, scanner, subscriptions, feed selection, or order execution logic changed. Tests committed but not executed.


## Batch 52 — integrated manager-to-readiness lifecycle regression

- Added an integration-level test that registers the actual CommonWebSocketManager
  transport observer against the dual-provider readiness bridge and drives active
  socket generations through the manager's own observer gate (without broker
  credentials or real network sockets).
- The scenario replaces an Angel shard socket, injects the retired generation's
  delayed close, then closes the current generation and injects a delayed OPEN.
  The readiness bridge must retain the current generation and must not
  resurrect a terminal socket epoch.
- In the same scenario, Shoonya authentication rejection is followed by a new
  authenticated epoch. Its recovery must remain independent of Angel's shard
  lifecycle. The test also asserts pairing, scanner fanout, and order
  eligibility remain disabled.
- This is deterministic integration coverage of callback plumbing and
  generation fencing only. It does not prove broker authentication,
  subscription acknowledgement, feed readiness, parity, live-market behavior,
  or scanner cutover. Existing Box Spread and IV Scanner behavior is untouched.
- Test is committed but has not been executed in this session; exact-head CI,
  full regression, live broker validation, merge, and deployment remain open.


## Batch 53 — targeted CI coverage for readiness lifecycle

- Extended the existing targeted common-feed pull-request workflow so changes to
  the provider readiness reducer/bridge and their regression tests trigger the
  targeted job.
- The job now compiles both readiness modules and runs
  `test_provider_readiness.py` plus `test_provider_readiness_bridge.py`,
  including the Batch 52 manager-observer integration regression.
- This is a CI wiring change only. It does not enable broker connections,
  alter feed selection, or change Box Spread / IV Scanner behavior.
- The workflow has been committed, but the exact-head workflow query currently
  returns no runs. Therefore the tests remain unexecuted/unverified; no pass is
  claimed. The visible Vercel status is a deployment-rate-limit failure and is
  not evidence about backend test results.


## Batch 54 — deterministic dual-broker reconnect-storm regression

- Added a deterministic callback-storm regression covering 64 Shoonya authentication
  epochs and 64 Angel lifecycle generations across three independent socket groups.
- Each Angel generation receives OPEN, ERROR, a forbidden same-generation OPEN,
  a replacement generation OPEN, and a delayed retired-generation CLOSED. The
  assertions require only the newest generation to remain open.
- Each Shoonya epoch authenticates and then times out; a delayed LIVE callback
  from that terminal epoch must not resurrect it. A fresh epoch 65 is then
  authenticated and must remain authoritative after a delayed epoch-64 failure.
- Assertions explicitly keep feed readiness, parity, scanner fanout, pairing,
  and order eligibility fail-closed because real subscription ACK and Angel
  authentication evidence are still unavailable.
- This is deterministic reducer/bridge stress coverage, not a threaded race
  proof, broker integration, or live-market validation. The test is included
  in the targeted CI workflow added in Batch 53, but has not been executed
  locally or confirmed by an exact-head CI run.


## Batch 55 — real threaded dual-provider lifecycle concurrency

- Added a real Python-thread regression that concurrently drives three independent
  Angel socket groups and one Shoonya authentication-epoch stream through the
  shared readiness bridge.
- Each Angel worker runs 40 OPEN/ERROR/replacement-OPEN/late-retired-CLOSED
  sequences. The Shoonya worker runs 40 AUTHENTICATING/LIVE/AUTH_TIMEOUT/late-LIVE
  sequences. A barrier starts all four workers together; bounded joins ensure
  a deadlock or stuck worker fails the test instead of hanging the suite.
- Assertions verify each Angel group retains only its final replacement
  generation, Shoonya retains its final terminal timeout epoch, and no provider
  becomes feed-ready, parity-eligible, scanner-enabled, paired, or order-eligible.
- This is actual threaded concurrency against the reducer/bridge, but not a
  live broker, manager shutdown, SDK transport, or throughput test. The test
  is committed but has not been executed in this session.
- No production runtime code, Box Spread/IV Scanner callbacks, calculations,
  subscriptions, or feed-selection behavior changed.


## Batch 56 — manager shutdown versus late transport callback race

- Added a threaded regression using the real CommonWebSocketManager.close()
  and transport-observer gate with a deliberately blocked fake socket close.
- The test starts shutdown on a separate thread, waits until socket retirement
  is in progress (after manager state has been atomically closed/cleared), then
  injects late OPEN and CLOSED notifications for the retired generation.
- It verifies late OPEN cannot resurrect the Angel readiness generation,
  the manager's retirement gate permits only the matching retiring CLOSED
  observation, and shutdown completes after the fake SDK close is released.
- This covers manager shutdown/callback ordering without broker credentials,
  network access, or live SDK transport. It is not proof of broker behavior or
  an actual SmartAPI SDK shutdown under network failure.
- The test is committed but not executed in this session. Production manager
  behavior is unchanged; Box Spread and IV Scanner callback/feed behavior,
  subscriptions, calculations and order routes remain untouched.
