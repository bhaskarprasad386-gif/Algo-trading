# Batch 0 — Broker Read-Only Reconciliation and Trading Adapter Audit

**Status:** Source audit only; no broker credentials used and no orders submitted.  
**Audited source:** current `main` at the time of this correction.

## 1. Capability matrix — distinguish history adapters from live trading

| Broker | Read-only order/trade snapshot code | Registered in `BrokerRegistry` | Place/cancel | Modify | Current verified capability |
|---|---|---|---|---|---|
| Angel One | Yes: current-day orderBook + tradeBook | Yes; sole registry entry | Explicitly fail closed with `NotImplementedError` | Not implemented | Authentication + read-only current-day history |
| FYERS | Yes: date-range orderhistory + tradehistory reports; current-day adapter books also exist | No | Explicitly fail closed | Not implemented | Injected-client auth/profile identity check + closed-day paginated reports |
| Shoonya | Yes: current-day order book + trade book | No | Explicitly fail closed | Not implemented | Injected-client UID/account identity check + read-only current-day history |
| Zerodha | Yes: current-day orders + trades | No | Explicitly fail closed | Not implemented | Injected-client profile identity check + read-only current-day history |
| m.Stock | Yes: current-day orders + tradebook | No | Explicitly fail closed | Not implemented | Type A OTP/TOTP session + read-only current-day history |

**Important correction:** FYERS, Shoonya, Zerodha and m.Stock do have provider-specific read-only order/trade history adapters in `backend/app/brokers/`. They are not registered as general broker trading adapters. The earlier statement that those adapters did not exist was too broad and is corrected here.

The current `BrokerRegistry` registers only `AngelOneAdapter`. The generic `BrokerAdapter` protocol covers connect/place/cancel/disconnect and does not itself describe the provider-specific history operation.

A separate common read-only contract already exists: `BrokerHistorySnapshotProvider` and `BrokerHistoryWindow` in `backend/app/brokers/history.py`. Five providers implement it: Angel One, FYERS, Shoonya, Zerodha and m.Stock. The structural conformance tests are in `backend/tests/test_broker_history_provider_conformance.py`; window invariants are tested in `backend/tests/test_broker_history_contract.py`. This audit does not propose recreating that contract.

## 2. What the read-only adapters actually prove

- Each provider requires an authenticated session/client and checks the requested account identity against the authenticated profile/session before returning a snapshot.
- Each history implementation requires both order and trade book/report responses; failed or malformed responses raise a provider-specific history-unavailable error rather than being silently represented as an empty book.
- Angel One, Shoonya, Zerodha and m.Stock history providers accept only the complete current IST calendar-day window. FYERS report provider accepts closed IST calendar-day ranges and paginates order-history and trade-history reports; FYERS reports exclude pending orders, so they are not a current-day open-order substitute.
- The normalized snapshot contains broker order ID, symbol, side, order quantity, filled quantity, broker trade ID, order linkage and trade quantity.
- The common window carries timezone-aware requested bounds, page count, continuation state and a provider-window label. Completeness is false while the snapshot is incomplete or pagination remains.
- These adapters do not implement live order placement, modification, cancellation, streaming order callbacks, positions, funds/margin, or a full partial-fill recovery workflow.

## 3. Registry and execution safety

- `BrokerRegistry.__init__` registers only `AngelOneAdapter`; the other read-only history providers are instantiated through their own provider/session paths, not via this trading registry.
- All five adapters' order mutation methods are explicitly tested to fail closed. This is a safety gate, not evidence that live trading is supported.
- No broker is certified for production Live execution by this source audit. Live remains OFF.
- The existing Shoonya market-data WebSocket/supervisor is separate from the Shoonya read-only order/trade history adapter. Market-data presence is not order execution or durable reconciliation.
- Do not change shared market-data providers, scanner behavior, or WebSocket subscriptions as part of this adapter audit.

## 4. Remaining certification gaps

Before any broker can be called Live-capable, add and verify:

1. Capability-aware composition of the existing read-only history protocol with positions, funds/margin, streaming events and separately gated order mutations.
2. Durable authenticated account/session binding and restart/refresh/revocation behavior.
3. Provider-specific pagination/completeness guarantees and explicit trading-date handling for order/trade books, including reconciliation of FYERS report exclusions with pending/current-day orderbook data.
4. Order status normalization, modify semantics, cancel races, rejected orders, partial fills, trade corrections/busts and reconnect recovery.
5. Deterministic client order IDs and a safe UNKNOWN/reconciliation-required outcome after ambiguous network timeouts.
6. Per-account rate limits, retry-after handling, secret redaction, encrypted secret references and audit trails.
7. Multi-account isolation tests for two accounts at one broker and across brokers.
8. Broker-approved non-production certification for place/modify/cancel and multi-leg recovery.
9. A separately approved release gate before any Live routing can be enabled.

## 5. Safety decision

Current verified state is **read-only reconciliation capability in five provider-specific history implementations sharing a common typed contract**, not full broker trading support. Angel One alone is registered in `BrokerRegistry`; all five brokers' order mutations fail closed. No broker credentials, market-data sockets, scanner code, schema, deployment, or runtime settings were changed by this audit.
