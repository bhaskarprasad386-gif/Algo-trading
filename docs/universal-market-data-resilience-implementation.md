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

## First implementation slice

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
