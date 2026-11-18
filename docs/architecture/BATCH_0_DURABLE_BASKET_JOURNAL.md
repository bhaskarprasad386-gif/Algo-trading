# Batch 0 — Durable SQLAlchemy Basket Journal

## Scope

This change adds a SQLAlchemy-backed journal for the multi-leg basket coordinator.
It reuses the repository's database and persists one account-scoped coordinator
claim, its client order IDs, broker acknowledgements, reconciliation evidence,
and append-only state events.

## Safety properties

- A unique (account_id, intent_id) constraint arbitrates submission claims
  across workers. A duplicate or abandoned claim is never taken over.
- The transition from CLAIMED to SUBMITTING is one-way. Repeated starts
  cannot replace the deterministic client order IDs.
- Every broker acknowledgement must refer to a client order ID recorded for
  that basket.
- FILLED requires authoritative order/trade/position reconciliation, a
  terminal broker order for every expected client ID, and non-zero fills.
- Freeze and manual-review decisions are durably recorded.
- The journal does not place orders, retry unknown submissions, or enable Live
  execution.

## Explicit remaining boundary

This journal is the durable entry-coordinator journal only. It is not yet the
RecoveryJournal implementation and does not provide a durable recovery budget,
exclusive instrument lease, persistent account/strategy kill switch, or
risk-reservation lifecycle. Those remain mandatory before emergency recovery
can be wired to a production broker adapter.

The current completion method independently checks complete reconciliation and
positive fills; the coordinator remains responsible for comparing each
reconciled fill quantity with its exact requested leg quantity before calling
completion. Production integration should additionally bind expected quantities
to the journal row so the durable layer can independently enforce that equality.

No broker adapter is registered or enabled by this change. No credentials,
live orders, deployment, service restart, scanner, market-data WebSocket, or
subscription configuration are touched.
