# Batch 0 — Broker Trading Adapter and Market-Data Separation Audit

**Status:** Source audit only; no credentials used and no broker orders submitted.  
**Audited base:** `main` at `8da655f0c287251cf1602cae484714a7c26fcf15`.

## 1. Trading adapter capability matrix

| Broker | Trading adapter registered | Connect/auth code | Place/modify/cancel | Order/trade/position reconciliation | Current conclusion |
|---|---|---|---|---|---|
| Angel One | Yes — sole registry entry | SmartAPI login with API key, client code, password, TOTP; session SDK object held in process memory | `place_order` and `cancel_order` explicitly raise `NotImplementedError`; no modify implementation | Not defined by current `BrokerAdapter` protocol | Authentication connector only; not a live trading adapter |
| Fyers | No | No broker trading adapter found in `backend/app/brokers/` | None | None | Not implemented |
| Shoonya | No trading adapter | Separate Shoonya market-data WebSocket/supervisor and readiness evidence exist | No trading order adapter in registry | Market-data order-update observation is not a trading reconciliation implementation | Market-data/shadow diagnostics only; do not count as trading support |
| mStock | No | No adapter found | None | None | Not implemented |
| Zerodha | No | No adapter found | None | None | Not implemented |

The current registry registers only `AngelOneAdapter`. The broker connect route accepts only `angel_one` and Angel One-shaped credential fields. The provider-neutral `BrokerAdapter` protocol currently exposes only connect/place/cancel/disconnect and does not define typed modify, order lookup, order/trade history, positions, funds/margin, streaming updates, or reconciliation.

## 2. Account/session isolation findings

- Broker session dictionary and connection metadata are keyed by `(user_id, broker)`, so only one account per broker/user can be represented.
- The stored adapter object is a mutable SDK session object in process memory. There is no durable per-account session registry or account foreign key.
- The durable Box real-owner state is singular and bound to one broker account fingerprint. It intentionally blocks account switching without an explicit reset/review.
- Connect flow disables real trading after successful login. Real-trading enable endpoint returns HTTP 503 until durable ownership and execution safety controls exist.
- Disconnect/kill-switch paths fail closed, but process-local session metadata is not a multi-worker account/session system.
- A broker-side client code/fingerprint must not be treated as a user authorization token. Every future account action must resolve the account from authenticated owner scope and verify account ownership.

## 3. Market-data versus trading separation

Shoonya files under `backend/app/market_data/` implement market-data transport, normalization, readiness evidence, and a supervisor. The inspected supervisor explicitly describes itself as isolated shadow diagnostics and does not route its quotes into scanner/order consumers. The provider readiness bridge is read-only and does not forward quotes to scanners, persistence, pairers, or order execution.

Therefore:
- Shoonya market-data WebSocket presence does not establish Shoonya trading support.
- Broker order-update-shaped market-data callbacks must not be mistaken for a durable order/trade reconciliation feed.
- Do not change shared market-data providers or WebSocket subscriptions as part of trading adapter work.
- Future trading adapter sessions must be separate from market-data socket ownership and keyed by stable account identity.

## 4. Required adapter certification before any broker is called supported

Each broker adapter needs a provider-specific evidence pack covering:
1. Authentication, refresh, expiry, logout/revocation, and restart behavior.
2. Account identity proof and multiple simultaneous accounts at the same broker.
3. Place/modify/cancel request validation and broker-specific product/order-type support.
4. Deterministic client order IDs and behavior on network timeout after request submission.
5. Order status, order book, trade book, fills, positions, funds, and margin reconciliation.
6. Partial fill, cancel race, rejection, duplicate callback, trade correction/bust, and reconnect behavior.
7. Rate limits, throttling, retry-after semantics, and idempotent recovery.
8. Secret redaction, encrypted secret references, audit trail, and per-account isolation tests.
9. Paper/mock contract tests plus broker-approved test/sandbox validation where available.
10. Failure-mode tests proving that ambiguous outcomes become `UNKNOWN/RECONCILIATION_REQUIRED`, never blind resubmission.

A broker must not appear as a supported live provider merely because its market-data feed, SDK dependency, or login call exists.

## 5. Implementation sequence recommendation

1. Define and test account-scoped provider-neutral order/event/reconciliation contracts without touching scanner/feed code.
2. Add durable account/session metadata and secret-reference handling; keep all order routes disabled.
3. Implement one broker adapter at a time, starting with Angel One only after order-book/trade-book/position reconciliation semantics are proven.
4. Add multi-account isolation tests using two independent mock accounts for the same broker and cross-broker accounts.
5. Certify place/modify/cancel/reconcile and partial multi-leg recovery in a broker-approved non-production environment.
6. Only after a separate explicit release approval may any Live routing gate be considered.

## 6. Safety decision

No current broker is certified for production Live order execution by this source audit. Angel One currently supports authentication only; Fyers, Shoonya, mStock, and Zerodha do not have trading adapters in the broker registry. Live remains OFF. No broker credentials, market-data sockets, scanner code, schema, deployment, or runtime settings were changed.
