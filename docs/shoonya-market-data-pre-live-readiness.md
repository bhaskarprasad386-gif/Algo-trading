# Shoonya Market-Data Adapter — Pre-Live Readiness

## Scope and safety boundary

This adapter is an isolated market-data transport. It does not place, modify, or cancel orders. It is not wired into the production provider selector or any existing scanner runtime by this change. Angel One remains the active provider unless a separately reviewed integration explicitly selects Shoonya.

The adapter must remain shadow/paper-only until the live validation checklist below is complete. Never commit Shoonya credentials, session tokens, TOTP seeds, or broker responses containing secrets.

## Implemented in this PR

- Noren WebSocket authentication handshake; subscriptions are sent only after a positive `ak` acknowledgement.
- TLS certificate verification, bounded exponential reconnect with per-attempt jitter to prevent synchronized reconnect storms, and reconnect stability reset only after an authenticated stable session.
- `tk` initial snapshots and `tf` incremental updates merged per instrument and per connection epoch.
- Canonical market-data normalization with exchange/token identity checks and epoch seconds/milliseconds/microseconds/nanoseconds conversion.
- Per-instrument exchange-time ordering guard: strictly older ticks are rejected, equal-second quote updates remain valid, and rejected frames cannot mutate the accepted snapshot.
- Health diagnostics expose an out-of-order tick counter for shadow validation.
- 500-token hard cap with atomic batch validation.
- Serialized subscription/unsubscription writes.
- Independent bounded tick and order-event queues. Authenticated Shoonya sessions deliver `om` order updates automatically; this adapter does not send a separate `t: o` subscription. Either queue overflow is treated as a feed-integrity fault, counted, surfaced in health state, and forces socket closure/reconnect. Order-event overflow additionally requires broker order-book reconciliation.
- Desired subscriptions survive reconnect epochs and are replayed only after the replacement socket receives a positive authentication acknowledgement.
- Callback isolation, connection health counters, shutdown deadline, and stale-socket callback protection.
- Authentication-handshake watchdog: configurable `SHOONYA_MARKET_DATA_AUTH_TIMEOUT_SECONDS` (default 15 seconds); a transport-open socket without a positive `ak` acknowledgement is closed and reported as `AUTH_TIMEOUT`, then uses the existing bounded reconnect path.
- Authenticated silent-frame watchdog: configurable `SHOONYA_MARKET_DATA_FRAME_TIMEOUT_SECONDS` (default 30 seconds), one-second freshness checks, explicit `DEGRADED` state, and forced socket close to enter the existing bounded reconnect path. The independent three-second application heartbeat remains unchanged.
- Health diagnostics expose last inbound frame age, configured frame/auth timeouts, auth age, and a stale-frame flag; a new connection epoch resets both auth and frame freshness before re-authentication.
- No credentials or live order-routing code.

## Runtime wiring implemented (shadow diagnostics only)

- `SHOONYA_MARKET_DATA_ENABLED=false` remains the default. Enabling it requires UID, account ID, access token, and validated queue/reconnect/token/frame-timeout limits. Subscription control additionally requires a separate `SHOONYA_MARKET_DATA_OWNER_PASSWORD` of at least 16 characters; it is checked at login time and is never used as a broker credential.
- FastAPI lifespan creates a separate `ShoonyaMarketDataSupervisor`; it owns its own `InstrumentRegistry` and `ShoonyaWebSocket`, and is stopped during application shutdown.
- The supervisor does not open a socket merely because the application starts. A separately registered/subscribed instrument set is required before it starts the adapter.
- `GET /api/v1/market-data/shoonya-health` exposes sanitized state, queue/freshness diagnostics, and explicit `shadow_only`, `order_routing_enabled=false`, and `strategy_fanout_enabled=false` flags. It never returns credentials.
- The existing on_tick callback contract remains unchanged. An additional optional read-only normalized tick observer receives the immutable MarketDataRecord, feed epoch, authenticated flag, and monotonic receive timestamp from the existing bounded dispatch queue; it adds no second queue and is not wired to a parity worker by default.
- The observer is invoked only if its captured feed epoch is still current and authentication remains valid at dispatch time. Observer exceptions are isolated from legacy tick delivery; a failing legacy consumer also cannot suppress the optional observer. A stale tick whose epoch changes during normalization is rejected before it mutates the replacement session snapshot.
- Tick and order callbacks otherwise continue to update bounded process-local counters only. There is no strategy fan-out, persistence, provider selection, or order action.
- `POST /api/v1/market-data/shoonya/subscriptions` and `DELETE /api/v1/market-data/shoonya/subscriptions` accept explicit instrument descriptors and require a separate short-lived Shoonya owner bearer session, obtained from `POST /api/v1/market-data/shoonya/owner-auth/login`. The POST is rejected unless the server-side Shoonya toggle is enabled; the DELETE remains available to release subscriptions while the supervisor exists.
- Subscription requests are limited to 500 instruments, validate exchange/instrument types and complete option metadata, reject duplicate exchange/token identities before mutation, and are isolated from Angel One's registry and callbacks.
- Adapter transport logs, public health diagnostics, and state reasons redact session credentials; subscription control requires the dedicated short-lived Shoonya owner bearer token and fails closed if the Shoonya server toggle is OFF.
- Supervisor lifecycle operations are serialized. If adapter subscription or worker startup fails, the supervisor attempts adapter cleanup and rolls back both its descriptor set and inactive registry entries; successful unsubscribe releases inactive registry entries.
- The environment toggle remains the hard ON/OFF gate; changing it requires a controlled application restart. The Shoonya owner-authenticated API only adds/removes shadow subscriptions; it cannot enable Shoonya or activate trading.

The following remain intentionally out of scope until a separately reviewed provider integration: selecting Shoonya for an existing scanner, automatic instrument-master translation, UI ON/OFF control, and live broker session validation. Angel One and existing scanners are not modified.

## Pre-live automated validation

- Backend and integration CI green on the exact PR head.
- Unit tests cover auth success/rejection, pre-auth event rejection, tick merge, invalid identity/timestamps, epoch-unit conversion, canonical exchange timestamp propagation, out-of-order replay rejection and snapshot integrity, reconnect subscription replay after auth, tick and order queue overflow fail-closed behavior and fault-state preservation, subscription cap/atomicity, concurrent subscribe/unsubscribe, reconnect epochs, stale callbacks, authentication-ack timeout, silent-frame watchdog timeout/recovery, shutdown timeout, and worker restart safety.
- Static review confirms no existing provider/scanner/order path is changed.
- Focused regression tests cover optional observer metadata, retired-epoch suppression, stale-epoch snapshot mutation prevention, and observer isolation from a failing legacy consumer; these tests are committed but must still run in exact-head CI.
- Verify logs redact credentials and access tokens.
- Verify no test depends on a live broker, wall-clock sleeps, or network credentials.

## Live validation — deliberately not part of this stage

Only after operator approval and credentials are supplied:
1. Connect using a dedicated Shoonya test session during market hours.
2. Confirm authentication acknowledgement and server heartbeat behavior.
3. Subscribe to a small, known instrument set and compare timestamps/LTP/bid/ask with Shoonya's official terminal.
4. Verify tick freshness, reconnect recovery, token replay, unsubscribe, and queue-drop alarms.
5. Run shadow-only alongside the existing feed; compare records without publishing to strategies or sending orders.
6. Stop the adapter and confirm all workers terminate cleanly.
7. Review the evidence before enabling any production provider selection.

## Go / no-go

**No-go** if authentication is not explicit, timestamps are absent/stale, instrument identity mismatches, queue drops occur, worker shutdown times out, credentials appear in logs, or data differs materially from the broker terminal. No production deployment or live order routing is authorized by this PR.
