# Universal Angel Market-Data Resilience — Implementation Plan

Status: implementation in progress on `feat/universal-market-data-resilience`. No merge, production activation, or VPS deployment is authorized by this branch.

## Target

- One process-owned Angel One WebSocket manager owns exactly three managed socket lanes while the market-data service is ON.
- One universal ingestion path validates, normalizes, timestamps, deduplicates, and publishes market data into a bounded canonical pool.
- Box Spread, IV Scanner, Synthetic and other strategy runners remain independent consumers. Their calculations, lifecycle controls, and strategy state are not merged into the shared runner.
- A failed lane's subscriptions are reassigned to healthy lanes within broker-confirmed limits. Recovery is gradual and generation-fenced.
- No strategy may instantiate an independent Angel socket outside the manager after migration is complete.

## Existing foundations verified in source

- `CommonWebSocketManager` already centralizes many Angel subscriptions, has a process-wide broker connect-admission lock/cooldown, per-generation fencing, tracked close workers, bounded SDK operation waits, reconnect supervision, and a three-socket / 1,000-token-per-socket software cap.
- `CommonStrategyMarketFeed` already exposes a shared process manager and canonical `MarketDataRecord` consumer interface.
- `BoundedMarketDataCache` already provides a TTL/LRU-bounded latest-snapshot cache and rejects older timestamps.
- `AngelShardCallbackAdapter` already provides per-shard bounded asynchronous callback dispatch, generation fencing, and fail-closed queue-overflow handling, but the process-wide shared manager was not enabling it.
- `SharedIndexMarketDataRunner` is a specialized index runner, not yet the universal source of all strategies' market data.
- The architecture review document identifies synchronous Angel callback fan-out as a concrete head-of-line risk and explicitly warns that ping/pong timeouts, network outages, and broker limits cannot be eliminated by software.

## User-locked socket affinity contract\n\nThe dedicated three-lane universe is explicitly constrained as follows:\n\n- Lane 0 / Socket 1: NIFTY spot and NIFTY WEEKLY CE/PE options only.\n- Lane 1 / Socket 2: BANKNIFTY spot and BANKNIFTY MONTHLY CE/PE options only.\n- Lane 2 / Socket 3: NIFTY and BANKNIFTY index futures only. No FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX, or other index may be placed on this lane.\n- Other expiry families are not silently coerced into a lane. Classification must come from the instrument-master expiry classifier.\n- Preferred lane metadata is distinct from temporary failover placement. Any failover implementation must retain the preferred lane and restore it only after a stable recovery window.\n\nThe source carries `expiry_type` through `InstrumentDescriptor`, exposes a deterministic `preferred_socket_lane` classifier, and the live allocator now consumes that metadata during reconcile. The allocator uses descriptor-level identity, preserves consumer route projection, and fails closed when the three-session cap cannot support isolated placement. Exact-head CI and full runtime consumer compatibility remain required before production activation.\n\n## First implementation slice

Enable the existing bounded per-shard asynchronous callback adapter only for the process-wide shared manager. Keep the generic `CommonWebSocketManager` default synchronous for direct tests and compatibility. This moves normalization, cache publication, and strategy callbacks off the SDK transport callback thread without changing strategy calculations.

Acceptance:
- The shared singleton reports asynchronous callback dispatch enabled.
- The queue remains bounded (current default capacity 2,048); overflow is visible and retires only the affected shard generation rather than silently dropping data.
- Existing direct manager callers retain the synchronous default.
- Add deterministic regression coverage for the production singleton configuration.
- Run backend CI on the exact PR head before any merge decision.

## Remaining implementation phases

1. **Three-lane ownership and distribution**
   - Explicit lane inventory and global token budget.
   - Three lane lifecycle states; no per-strategy sockets.
   - Stable weighted assignment with reserved failover headroom.
   - One-lane and two-lane outage redistribution; recovery rebalance with hysteresis.
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
- IV Skew Flip creates distinct consumer adapters, including mode-3 spot/options and mode-1 equity/futures feeds. Those mode differences are real allocator inputs and cannot be collapsed by a naive lane-only key.
- Other common-feed consumers found include commodity recording, Box Spread depth/shadow, calendar, and generic strategy runtime registrations. They must remain independent owners when allocation changes.

### Direct-socket inventory and unresolved owners

- `backend/app/market_data/live_cash_future_stream.py` still contains a legacy collector that directly creates two sockets (NSE and NFO). The application lifespan path inspected above starts `LiveCashFutureCommonRunner`, not this legacy collector. Its remaining references include health reporting/imports and a dedicated verification workflow; do not delete or migrate it until every external/manual invocation is ruled out.
- `backend/app/market_data/engine.py` still has a generic fallback that constructs `MarketDataWebSocket()`. The current search did not establish a production lifespan activation path for that fallback. Keep it out of the common-feed migration until a concrete runtime caller is identified.
- `live_synthetic_stream.py` retains a socket-factory fallback for explicitly injected/non-shared operation; its common-manager path disables private reconnect and delegates recovery to the shared manager.

### Allocator constraints confirmed

- Socket identity is `(mode, shard)`, so the global three-session cap must count groups across all modes.
- Registry subscriptions collapse multiple consumer modes to the maximum mode for a shared instrument. That is an existing compatibility rule; changing it requires a dedicated migration and tests because consumers may otherwise observe altered broker mode.
- The locked affinity metadata is instrument-level, while consumers span multiple underlyings and may subscribe in different modes. A safe implementation must resolve canonical descriptors per broker instrument, apply locked affinity only to compatible subscriptions, preserve consumer routes, and reject incompatible placement before mutating live assignments.
- The live allocator now uses `preferred_socket_lane` by canonical `(mode, exchange_type, token)` identity and applies the global session cap before committing assignments/routes. Locked-lane failover redistribution and stable recovery rebalance remain pending; the current integration pins preferred placement and does not yet implement outage redistribution.

### Next acceptance gate

After the initial live affinity integration, continue deterministic tests for: canonical descriptor lookup across exchange/token identities; cross-mode group-cap rejection without assignment mutation; shared-instrument multi-consumer mode behavior; and failover/recovery placement preserving consumer routes. Add failover redistribution only after preferred-lane preservation and global capacity can be proven without socket side effects.


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
