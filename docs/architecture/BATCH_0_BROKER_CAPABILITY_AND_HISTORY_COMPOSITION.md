# Batch 0 — Broker Capability and History Composition

## Scope

This slice separates three concepts that must not be conflated:

1. A broker has a read-only order/trade history provider.
2. A broker has a registered executable adapter/session.
3. A broker is certified for real order mutation.

The capability matrix and history-provider registry cover all five requested providers:
Angel One, Zerodha, FYERS, Shoonya and m.Stock. This does **not** register the
four non-Angel-One providers in the shared order-routing registry and does not
authorize place, cancel, modify, or Live execution.

## Declared history coverage

| Broker | Supported evidence window | Limitation |
|---|---|---|
| Angel One | Current IST trading day | SmartAPI orderBook and tradeBook must both succeed |
| Zerodha | Current IST trading day | Current Kite order/trade books only |
| FYERS | Closed IST calendar days | Paginated date reports exclude current-day pending/open orders |
| Shoonya | Current IST trading day | Current-day Order Book and Trade Book only |
| m.Stock | Current IST trading day | Current-day Order Book and Trade Book only |

A provider must reject unsupported windows and mismatched account/session identity.
A failed, malformed, or incomplete provider response must not be represented as
an empty complete snapshot.

## Composition and safety

- `BrokerCapabilityRegistry` is descriptive metadata; it grants no permissions.
- `BrokerHistoryProviderRegistry` composes a read-only provider only when the
  requested broker matches the authenticated session adapter's `name`.
- `BrokerRegistry` remains the executable adapter registry. It is not expanded
  by the history registry.
- The authenticated `GET /api/v1/brokers/capabilities` endpoint reports the
  distinction explicitly and always returns `execution_authorized: false`.
- All broker order mutation and Live certification flags remain false.

## Still not certified

This slice does not claim durable token/session restoration, provider-side
multi-page completeness for current-day books, full status/cancel-race
normalization, reconnect recovery, or deterministic UNKNOWN-order recovery.
FYERS current-day open/pending order evidence remains a separate requirement.
No broker order was placed, modified, or cancelled, and no deployment was run.
