# Universal Angel Market-Data Resilience — Implementation Plan

Status: code implementation and exact-head automated validation complete on `feat/universal-market-data-resilience`; external production acceptance evidence remains open. No merge, production activation, or VPS deployment is authorized by this branch.

## Target

- One process-owned Angel One WebSocket manager owns exactly three managed socket lanes while the market-data service is ON.
- One universal ingestion path validates, normalizes, timestamps, deduplicates, and publishes market data into a bounded canonical pool.
- Box Spread, IV Scanner, Synthetic and other strategy runners remain independent consumers. Their calculations, lifecycle controls, and strategy state are not merged into the shared runner.
- A failed lane is retired and recreated in-place under the same `(mode, shard)` identity. Strict user-locked affinity forbids cross-lane co-location or redistribution; healthy lanes remain untouched.
- No strategy may instantiate an independent Angel socket outside the manager after migration is complete.

## Existing foundations verified in source

- `CommonWebSocketManager` already centralizes many Angel subscriptions, has a process-wide broker connect-admission lock/cooldown, per-generation fencing, tracked close workers, bounded SDK operation waits, reconnect supervision, and a three-socket / 1,000-token-per-socket software cap.
- `CommonStrategyMarketFeed` already exposes a shared process manager and canonical `MarketDataRecord` consumer interface.
- `BoundedMarketDataCache` already provides a TTL/LRU-bounded latest-snapshot cache and rejects older timestamps.
- `AngelShardCallbackAdapter` already provides per-shard bounded asynchronous callback dispatch, generation fencing, and fail-closed queue-overflow handling, but the process-wide shared manager was not enabling it.
- `SharedIndexMarketDataRunner` is a specialized index runner, not yet the universal source of all strategies' market data.
- The architecture review document identifies synchronous Angel callback fan-out as a concrete head-of-line risk and explicitly warns that ping/pong timeouts, network outages, and broker limits cannot be eliminated by software.

## User-locked socket affinity contract

The dedicated three-lane universe is explicitly constrained as follows:

- Lane 0 / Socket 1: NIFTY spot and NIFTY WEEKLY CE/PE options only.
- Lane 1 / Socket 2: BANKNIFTY spot and BANKNIFTY MONTHLY CE/PE options only.
- Lane 2 / Socket 3: NIFTY and BANKNIFTY index futures only. No FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX, or other index may be placed on this lane.
- Other expiry families are not silently coerced into a lane. Classification must come from the instrument-master expiry classifier.
- Preferred lane metadata is distinct from temporary failover placement. Any failover implementation must retain the preferred lane and restore it only after a stable recovery window.

The source carries `expiry_type` through `InstrumentDescriptor`, exposes a deterministic `preferred_socket_lane` classifier, and the live allocator now consumes that metadata during reconcile. The allocator uses descriptor-level identity, preserves consumer route projection, and fails closed when the three-session cap cannot support isolated placement. Exact-head CI and full runtime consumer compatibility remain required before production activation.

## First implementation slice

Enable the existing bounded per-shard asynchronous callback adapter only for the process-wide shared manager. Keep the generic `CommonWebSocketManager` default synchronous for direct tests and compatibility. This moves normalization, cache publication, and strategy callbacks off the SDK transport callback thread without changing strategy calculations.

Acceptance:
- The shared singleton reports asynchronous callback dispatch enabled.
- The queue remains bounded (current default capacity 2,048); overflow is visible and retires only the affected shard generation rather than silently dropping data.
- Existing direct manager callers retain the synchronous default.
- Add deterministic regression coverage for the production singleton configuration.
- Run backend CI on the exact PR head before any merge decision.

## Pre-merge completion ledger — 2026-11-25

This ledger prevents implemented foundations from being confused with the remaining acceptance work.

| Workstream | Current evidence | Still required before merge |
|---|---|---|
| Three locked socket lanes | Descriptor-level affinity, consumer route projection, global three-session cap, same-lane recovery and deterministic regression coverage are present. | Final exact-head CI; prove all active consumer universes fit the locked three-session budget without hidden fourth groups. |
| Transport liveness | Transport thread, ping/pong, raw-frame, exchange-timestamp, subscription response/rejection, successful route and consumer-delivery health are separately exposed; bounded SDK operations and recovery supervision exist. | Fault-injection coverage for API/DNS/TLS/half-open/slow SDK cases and verify no false LIVE under each failure. |
| Canonical data pool | Canonical MarketDataRecord carries separate provider `exchange_timestamp_ns`, compatibility/event `timestamp_ns`, and optional local `received_at_monotonic_ns`; Angel One and Shoonya normalizers populate source/receipt clocks, common-feed ingress refreshes the receipt clock before fan-out, and the bounded cache/warm replay preserves all fields. | Confirm cache pressure and malformed-data acceptance tests against the final exact head; measure memory/queue high-water in soak. |
| Direct socket ownership | Repository-wide call-site audit found no application-lifespan construction of the legacy two-socket Cash-Future collector or generic `LiveMarketDataFeed`/`MarketDataEngine` fallback; active Box Spread, IV, Synthetic, Calendar and Cash-Future startup paths use shared-manager adapters. | External/manual invocation cannot be proven absent from repository source. Preserve dormant compatibility classes until owner confirms no external caller or explicitly authorizes retirement. |
| Fault injection / soak | Deterministic unit and integration regressions cover generation races, simultaneous lane recovery, delayed events, queue overflow, shutdown fencing and route preservation. | Complete transport fault matrix and representative soak with latency/memory/queue high-water evidence. |

A full backend pass is necessary but does not by itself satisfy the transport fault matrix, direct-owner inventory, broker entitlement confirmation, or representative production soak. No item is marked complete without exact evidence. The frontend is hosted from the VPS; frontend build/deployment is outside this backend market-data PR. No Cloudflare build, Workers, Pages, OpenNext, or Wrangler gate is required for PR #754.
## Remaining implementation phases

1. **Three-lane ownership and distribution**
   - Explicit lane inventory and global token budget.
   - Three lane lifecycle states; no per-strategy sockets.
   - Deterministic descriptor-level affinity and a global physical-session/token budget.
   - Same-lane socket recreation with generation fencing; no cross-lane redistribution or recovery rebalance under the locked topology.
   - Fail closed before registry/route mutation when the global session budget cannot represent all active `(mode, shard)` groups.
   - Do not exceed broker-confirmed connection/token limits.

2. **Transport liveness and API reachability**
   - Separate transport-open, ping/pong, inbound-frame, valid-quote, subscription-ack/rejection, and consumer-delivery health.
   - Bounded connect/handshake/subscription/close operations.
   - Detect half-open sockets and stuck SDK threads; replace only after retirement is tracked.
   - Jittered exponential backoff, global admission, cooldown, circuit breaker, and retry budgets.
   - Never mark LIVE based only on TCP/WebSocket OPEN or a successful local subscribe send.

3. **Universal canonical market-data pool**
   - One canonical latest quote per instrument and bounded short histories.
   - Source exchange timestamp and local monotonic receive timestamp kept separately.
   - Reject stale/out-of-order generations and invalid quote structures.
   - Queue/cache pressure metrics, explicit overflow policy, TTL, and memory limits based on measured production load.
   - Strategy-specific derived state remains outside the shared ingestion layer.

4. **Direct socket ownership migration**
   - Inventory every direct `MarketDataWebSocket` / `SmartWebSocketV2` construction and runtime activation path.
   - Migrate only confirmed active independent Angel owners through compatibility adapters.
   - Preserve Box Spread, IV Scanner, Synthetic, calendar, and other strategy outputs with replay parity.
   - Remove old owners only after tests prove no runtime path uses them.

5. **Fault-injection and soak**
   - Ping timeout, silent half-open, DNS/API unreachable, TLS/handshake stall, auth expiry, subscription rejection, broker 429, slow callback, queue saturation, simultaneous lane failures, late close, delayed health events, and reconnect races.
   - Verify one/two-lane failover, gradual recovery, no duplicate subscriptions, no stale quote overwrite, and no false LIVE.
   - Measure p50/p95/p99 receive-to-consumer latency and memory/queue high-water under representative load.

## Safety rules

- Do not combine strategy calculations into the universal runner.
- Do not invent broker capacity. Software caps remain conservative until official broker limits/account entitlements are verified.
- Do not add Redis or split systemd services until cross-process ownership is required and benchmarked.
- Do not alter trading/order execution behavior.
- Do not merge, deploy, or touch VPS without explicit approval.


## Runtime ownership audit — 2026-11-25

This audit distinguishes a class that can open sockets from a production lifecycle that actually starts it. Search results alone are not treated as proof of runtime activation.

### Confirmed application startup paths

- `backend/app/main.py` creates `SharedIndexMarketDataRunner` and starts its recovery task during application lifespan. Its three feed consumers share the process-wide `CommonWebSocketManager`.
- The live Cash-Future task is gated by `LIVE_CASH_FUTURE_DATA_ENABLED` and instantiates `LiveCashFutureCommonRunner`. That runner obtains `shared_common_manager()` and subscribes through the common registry; it is not the legacy two-socket collector.
- Calendar Spread is gated by `LIVE_CALENDAR_SPREAD_DATA_ENABLED` and starts `LiveCalendarSpreadOneSecondCollector`, which uses the common strategy feed.
- Synthetic and Box Spread have separate feature-flagged supervised tasks. Their underlying/option data paths use common-feed adapters; strategy logic remains separate.
- IV Skew Flip adapter classes have standalone defaults that differ (spot/futures/equity adapters default to mode 1), but the production `IVSkewFlipRuntime` explicitly injects the process-wide manager with mode 3 into all four feed families: spot, index futures, weighted equities, and options. The application startup path passes that shared manager. Therefore the earlier concern that production IV equities necessarily add mode-1 socket groups was not supported by the actual runtime wiring; those production consumers request mode 3. Preserve the standalone adapter defaults for compatibility and keep production mode assertions in regression tests.
- Other common-feed consumers found include commodity recording, Box Spread depth/shadow, calendar, and generic strategy runtime registrations. They must remain independent owners when allocation changes.

### Direct-socket inventory and unresolved owners

- `backend/app/market_data/live_cash_future_stream.py` still contains a legacy collector that directly creates two sockets (NSE and NFO). The application lifespan path inspected above starts `LiveCashFutureCommonRunner`, not this legacy collector. Its remaining references include health reporting/imports and a dedicated verification workflow; do not delete or migrate it until every external/manual invocation is ruled out.
- `backend/app/market_data/engine.py` still has a generic fallback that constructs `MarketDataWebSocket()`. The current search did not establish a production lifespan activation path for that fallback. Keep it out of the common-feed migration until a concrete runtime caller is identified.
- `live_synthetic_stream.py` retains a socket-factory fallback for explicitly injected/non-shared operation; its common-manager path disables private reconnect and delegates recovery to the shared manager.

### Application startup and mixed-mode compatibility — 2026-11-25

- `backend/app/main.py` constructs the shared-index runner at module initialization and starts `_shared_index_market_data_recovery_loop()` from application lifespan. This is an active startup path, not merely a diagnostic object.
- The same lifespan starts the IV recovery loop unconditionally; that loop respects the persisted IV Scanner ON/OFF control before creating `IVSkewFlipRuntime`. Cash-Future, Calendar Spread, Synthetic, and Box Spread remain feature-flagged lifecycle tasks and use common-feed adapters in their active paths.
- The adapter constructors' standalone defaults are not the production contract: `IVSkewFlipRuntime` passes `CommonStrategyMarketFeed(..., mode=3)` explicitly to its injected spot, futures, equity, and options adapters. The main application injects `shared_common_manager()` into that runtime. The source-level test `test_production_feed_manager_injection_isolates_all_iv_consumers_on_one_mode_three_socket` asserts this wiring.
- Consequently, the previously suspected mode-1 equity group is not created by the production IV runtime path. The locked shared-index owners and production IV consumers all request mode 3; the registry still aggregates modes per canonical instrument, and `(mode, shard)` remains the physical identity. Remaining compatibility risk is the total number of distinct shards/groups required by all active non-IV consumers, not a presumed mode-1 IV equity group.

### Allocator constraints confirmed

- Socket identity is `(mode, shard)`, so the global three-session cap must count groups across all modes.
- Registry subscriptions collapse multiple consumer modes to the maximum mode for a shared instrument. That is an existing compatibility rule; changing it requires a dedicated migration and tests because consumers may otherwise observe altered broker mode.
- The locked affinity metadata is instrument-level, while consumers span multiple underlyings and may subscribe in different modes. A safe implementation must resolve canonical descriptors per broker instrument, apply locked affinity only to compatible subscriptions, preserve consumer routes, and reject incompatible placement before mutating live assignments.
- The live allocator now uses `preferred_socket_lane` by canonical `(mode, exchange_type, token)` identity and applies the global session cap before committing assignments/routes. Recovery recreates a failed socket in the same `(mode, shard)` lane; cross-lane redistribution and recovery rebalance are intentionally excluded by the locked topology.

### Next acceptance gate

After the initial live affinity integration, continue deterministic tests for: canonical descriptor lookup across exchange/token identities; cross-mode group-cap rejection without assignment mutation; shared-instrument multi-consumer mode behavior; and failover/recovery placement preserving consumer routes. Continue testing same-lane retirement/recreation, global capacity rejection, mode isolation, and route preservation. Do not add cross-lane failover redistribution: it conflicts with the locked three-lane contract.


### Consumer-owner granularity audit — 2026-11-25

A follow-up inspection of `SharedIndexFeedLifecycle` and `ConsumerSocketAffinity` found that the existing affinity helper's owner model does not match the live lifecycle's consumer boundaries:

- The live lifecycle has three owners: `shared-index-market-data:spot`, `shared-index-market-data:options`, and `shared-index-market-data:futures`. The spot owner can hold both NIFTY and BANKNIFTY spot instruments; the options owner can hold NIFTY WEEKLY and BANKNIFTY MONTHLY options; futures are separately owned.
- The standalone affinity helper instead declares `nifty`, `banknifty`, and `options` owners, and its third locked slot is currently named `options`. That does not represent the requested lane 2 (NIFTY + BANKNIFTY futures), and its first two owners cannot be mapped one-to-one to the live spot/options consumers.
- Therefore, wiring the helper directly into reconcile would either split a consumer's subscriptions without preserving descriptor-level routes, or place mixed NIFTY/BANKNIFTY instruments together on the same consumer slot. Neither is acceptable.
- The allocator must plan by canonical instrument identity and descriptor, not by consumer name. It must then project each instrument's chosen `SocketGroup(mode, shard)` back to every subscribed consumer route. The futures lane must include only NIFTY and BANKNIFTY futures; option lane membership is split by underlying and expiry family.
- Before implementation, add tests proving one live consumer can route instruments to different lane groups while every consumer callback still receives exactly its own subscribed keys. Also test shared instrument subscriptions with multiple consumers/modes and exchange/token collisions without losing canonical identity.

This is a concrete blocker, not a reason to rename the helper's owner labels: the helper's owner-level abstraction must be replaced or bypassed by an instrument-level planner before live allocator integration.


## Live descriptor-affinity allocator integration — 2026-11-25

The live CommonWebSocketManager allocator now consumes InstrumentDescriptor.preferred_socket_lane during reconcile. It resolves affinity by (mode, exchange_type, token), then projects each instrument placement back to every subscribed consumer route. Consumer names are not used to infer the lane, so the combined options owner can route NIFTY weekly and BANKNIFTY monthly instruments to different sockets.

- NIFTY spot / NIFTY WEEKLY options resolve to shard 0.
- BANKNIFTY spot / BANKNIFTY MONTHLY options resolve to shard 1.
- NIFTY and BANKNIFTY futures resolve to shard 2.
- A broker exchange/token identity that maps to conflicting locked and unclassified descriptors is rejected before route/socket state is committed.
- Adaptive instruments are placed only outside lanes reserved by locked instruments. If that requires a fourth physical (mode, shard) group, the existing global three-session gate rejects the reconcile rather than contaminating a locked lane.
- snapshot() uses the same preferred-lane calculation as live reconcile, so reported required groups and roster locations cannot silently fall back to the legacy allocator.

This is a guarded allocator integration, not a production activation claim. The explicit capacity trade-off remains: with all three locked lanes occupied, other unclassified instrument universes cannot be added to this same three-session manager without a separately designed compatible allocation policy. The next acceptance step is exact-head CI and runtime consumer inventory validation; no merge, production activation, or VPS work is authorized.



## Allocator boundary and recovery follow-up — 2026-11-25

The deep review added four fail-closed safeguards after the live allocator integration:

- Pinned instruments are subject to the same per-socket token ceiling as adaptive placements. A locked lane that exceeds the configured 1,000-token software cap is rejected before assignments, routes, or sockets are committed; it is not split into a second shard because that would violate the dedicated-lane contract.
- A broker exchange/token pair must resolve to one canonical instrument descriptor. Even two descriptors that happen to classify to the same preferred lane are rejected rather than allowing one broker frame to fan out to distinct instrument identities.
- Diagnostic snapshots tolerate incomplete/placeholder subscription views during recovery. A monitoring read must not crash the recovery supervisor or consume pending failure groups.
- Recovery compares the full instrument-to-lane assignment plan as well as the physical socket-group set. A stale lane plan can therefore trigger reconciliation even when the same three physical group identities still exist.

These checks do not implement failover redistribution. They protect the strict preferred topology while failover/recovery policy is designed and tested separately.


### Strict-lane outage behavior

The three-lane contract forbids cross-lane co-location: Socket 1 remains NIFTY-only, Socket 2 remains BANKNIFTY-only, and Socket 3 remains NIFTY/BANKNIFTY-futures-only. Therefore failover must not temporarily migrate a failed lane's tokens onto another lane. The safe recovery behavior under this contract is to retire and recreate the failed physical session in the same `(mode, shard)` lane, preserving the other healthy sessions and restoring only that lane's token set. Any future cross-lane failover proposal would require an explicit change to the user's isolation requirement; it is not part of this implementation.


### Callback shutdown deadline fencing — 2026-11-25

A final callback-adapter review found that graceful `stop(drain=True)` could exceed its deadline semantically: after the join budget expired, the worker loop still drained queued batches and could continue the remainder of a dequeued batch after a caller had already received a timeout. The adapter now advances its generation and discards pending batches when the graceful shutdown budget is exhausted. A callback already executing remains non-cancellable, but every subsequent invocation is fenced. A deterministic regression test covers this case.


### Locked owner descriptor validation — 2026-11-25

The lifecycle entry points now validate the complete broker instrument identity before registry mutation: spot ownership accepts only the exact NSE NIFTY/BANKNIFTY index descriptors (including NSE key exchange/segment and matching symbols); futures ownership and rotation require NFO on both descriptor and instrument key. This prevents a correctly named underlying with a wrong instrument kind or exchange key from being routed into a reserved lane.

### Direct socket ownership — repository-wide call-site closure audit — 2026-11-25

- `SmartWebSocketV2` construction is confined to `MarketDataWebSocket`; that wrapper is the transport implementation used by the common manager.
- `LiveCashFutureOneSecondCollector` still contains a legacy NSE/NFO two-socket implementation, but the application lifespan constructs `LiveCashFutureCommonRunner`. Repository-wide call-site search found no in-repository constructor invocation of the legacy collector. Its public class remains available, so an external/manual caller cannot be ruled out from repository evidence alone.
- `MarketDataEngine` and `LiveMarketDataFeed` retain a standalone `MarketDataWebSocket` default. Repository-wide call-site search found only the feed's own default construction and test injection; no application lifespan or active scanner startup path constructs this legacy feed.
- `LiveSyntheticMarketDataStream` retains an explicitly injected/non-shared socket-factory fallback. The production synthetic runner supplies the shared manager path; the fallback is retained for standalone compatibility.
- These dormant entry points are not silently deleted: the remaining gate is owner confirmation that no external/manual script imports them. Removing them without that confirmation could break an out-of-repository runtime.

### Canonical receive-clock contract — 2026-11-25

- `MarketDataRecord.exchange_timestamp_ns` is nullable and only populated when the provider supplied a valid source timestamp. `timestamp_ns` remains the compatibility/event timestamp and may use a wall-clock fallback for Angel One. `received_at_monotonic_ns` is a distinct local monotonic clock and is never compared numerically to wall-clock timestamps.
- Angel One and Shoonya normalizers populate source and receive clocks for standalone consumers. The common Angel manager overwrites the receive clock with the ingress clock captured before raw callback fan-out, then the shared latest-snapshot cache persists all three clocks and warm-start replay restores them.
- Existing consumers remain source-compatible because the new field is optional; direct synthetic/test records may omit it until they pass through a provider normalizer.
- Regression tests cover both provider normalizers, missing-source timestamp behavior, validation, common-manager stamping, cache persistence and warm replay.


## Deployment boundary — VPS frontend

- The frontend is served from the VPS using the standard Next.js runtime; it is not deployed through Cloudflare Workers or Pages.
- PR #754 changes backend market-data ingestion, allocation, lifecycle, and regression tests only. It does not change frontend deployment, reverse-proxy configuration, backend service deployment, or VPS state.
- Frontend deployment cleanup belongs in its own explicitly scoped change. It must preserve the existing VPS Next.js start/build process and the frontend-to-backend WebSocket origin contract.
- CI for this PR is GitHub Actions backend/integration/targeted-feed validation. External Cloudflare or Vercel deployment status is not an acceptance gate for this PR.


## Final code-gate closure batch — 2026-11-25

### Exact-head automated evidence

Validated against PR head `7cad4b1da229d096a5af68d2ba208717b1144036`:

- Backend Tests run `45534943421`: all jobs successful, including the full backend test job and Fast Broker Safety Gate.
- Integration Tests run `45534943305`: all jobs successful, including integration, IV independent instrument-master network validation, instrument-master transport regressions, common-feed targeted regressions, and Box Spread master shutdown/cache regressions.
- Targeted Common Feed Tests run `45534943278`: successful.
- Off-Market Runtime Verification run `45534943459`: contract test successful; VPS runtime verification was skipped and is not claimed as passed.

These results close the exact-head automated code-validation gate. They do not establish broker entitlement, external/manual caller absence, or production soak behavior.

### Remaining external acceptance evidence (not code defects)

1. **Broker session/token entitlement:** obtain written confirmation for the account's permitted simultaneous physical WebSocket sessions and per-session token limits. The software guard remains three sessions and 1,000 tokens per socket; do not increase either from assumptions. Record the broker response/account entitlement evidence before production activation.
2. **External/manual socket callers:** repository startup-path inspection found active application owners using the shared manager, while a legacy two-socket Cash-Future collector and generic engine fallback remain in source without a confirmed active application-lifespan path. The source audit cannot rule out operator scripts, external imports, or manually launched processes. Obtain operator/runtime inventory confirmation before retiring compatibility code; do not delete these classes speculatively.
3. **Representative production soak:** run only after entitlement and runtime ownership are confirmed and activation is separately authorized. Capture duration, instrument/token counts by lane, p50/p95/p99 ingress-to-consumer latency, process RSS, callback queue high-water/overflow, reconnect/failover counts and durations, stale/out-of-order rejection counts, and per-lane delivery gaps. Exercise the approved fault matrix and attach raw monitoring evidence. No production soak was run by this PR batch.

### Explicit scope boundary

- No Cloudflare, Vercel, frontend build/deploy, VPS, or reverse-proxy operation is part of PR #754 acceptance. The frontend is VPS-hosted; this PR is backend market-data scope.
- No Shoonya credential/readiness work, strategy formula, order execution, or scanner behavior was changed as part of this gate closure.
- PR remains draft until the external evidence above is supplied and reviewed. No merge or production activation is implied by green CI.


## External gate execution packet — 2026-11-25

Created `docs/PR_754_EXTERNAL_GATE_EVIDENCE_TEMPLATE.md` as the single evidence intake and operational runbook for the three external gates. It defines account-specific broker confirmation fields, a read-only VPS process/service inventory, production soak preconditions, per-lane metrics, controlled fault scope, no-go conditions, and a reviewer/approver record. It explicitly forbids sharing credentials or raw broker payloads and does not treat CI/off-market replay as production soak evidence.

The user authorized work on the remaining external gates. Repository-side preparation is complete, but the connected GitHub capability has no access to the broker support mailbox/account entitlement records or the VPS process namespace, and no production telemetry/soak execution channel is connected in this session. Therefore no account-specific broker entitlement, host operator attestation, or production soak result is claimed. Those evidence-dependent gates remain open until the account holder/operator supplies the packet fields and raw artifacts. No production service, VPS, broker session, deployment, or trading process was started or modified from this session.
