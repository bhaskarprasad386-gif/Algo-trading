# Shoonya Market-Data Adapter — Pre-Live Readiness

## Scope and safety boundary

This adapter is an isolated market-data transport. It does not place, modify, or cancel orders. It is not wired into the production provider selector or any existing scanner runtime by this change. Angel One remains the active provider unless a separately reviewed integration explicitly selects Shoonya.

The adapter must remain shadow/paper-only until the live validation checklist below is complete. Never commit Shoonya credentials, session tokens, TOTP seeds, or broker responses containing secrets.

## Implemented in this PR

- Noren WebSocket authentication handshake; subscriptions are sent only after a positive `ak` acknowledgement.
- TLS certificate verification, bounded exponential reconnect, and reconnect stability reset only after an authenticated stable session.
- `tk` initial snapshots and `tf` incremental updates merged per instrument and per connection epoch.
- Canonical market-data normalization with exchange/token identity checks and timestamp validation.
- 500-token hard cap with atomic batch validation.
- Serialized subscription/unsubscription writes.
- Independent bounded tick and order-event queues; order-event overflow is surfaced as a feed fault and forces socket closure for reconciliation.
- Callback isolation, connection health counters, shutdown deadline, and stale-socket callback protection.
- No credentials or live order-routing code.

## Required environment / deployment decisions before runtime wiring

The production application does not yet select Shoonya from configuration. Before adding that wiring, define and review:
- An explicit `SHOONYA_MARKET_DATA_ENABLED=false` default.
- Credentials sourced only from the deployment secret store: user id, account id, and current access token.
- An explicit shadow/paper-only mode; no implicit fallback from Angel One.
- A single-account ownership rule and an operator-visible ON/OFF control.
- Startup validation that refuses to start when enabled but credentials or required instrument metadata are missing.
- A shutdown hook that stops the adapter and reports any surviving worker.
- Health/status endpoint fields for connected, authenticated, last frame/tick age, reconnect count, queue depth/drops, and last error.
- A clear provider identity on every normalized record so data cannot be mistaken for Angel One.

## Pre-live automated validation

- Backend and integration CI green on the exact PR head.
- Unit tests cover auth success/rejection, pre-auth event rejection, tick merge, invalid identity/timestamps, queue overflow, subscription cap/atomicity, concurrent subscribe/unsubscribe, reconnect epochs, stale callbacks, shutdown timeout, and worker restart safety.
- Static review confirms no existing provider/scanner/order path is changed.
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
