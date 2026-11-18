# Batch 0 — Unified restart recovery inventory

## Scope

`UnifiedRestartRecoveryInventory` joins the durable basket coordinator journal,
the execution-intent ledger, and the durable recovery controls by the stable
`(account_id, intent_id)` identity. It provides a bounded, deterministic,
read-only startup inventory and one atomic fail-closed freeze operation.

## Startup contract

1. Enumerate account-scoped non-terminal basket claims, execution intents, and
   recovery controls in deterministic oldest-update order.
2. Merge records sharing the same account and intent identity; never merge
   across accounts.
3. Treat CLAIMED/SUBMITTING/FROZEN basket records, non-terminal execution
   intents, and risk-reserved non-FLAT recovery controls as unresolved.
4. Before any future entry is considered, the caller must reconcile broker
   orders, trades, and positions using a broker-specific authoritative adapter.
5. An unresolved/ambiguous intent is frozen and its strategy kill switch is
   latched transactionally. Risk reservation remains held.
6. This module never sends/cancels orders, replays submissions, releases risk,
   declares positions flat, or clears a kill switch.

## Failure behavior

- Missing identity records fail closed; no synthetic intent is invented.
- A verified FLAT recovery record is terminal and cannot be reopened.
- Repeated freeze requests do not duplicate the basket freeze event.
- Recovery events record `broker_action_taken: false` to make the boundary
  explicit; broker reconciliation remains a separate approved operation.

## Remaining certification

The service is a persistence/inventory primitive, not a broker restart worker.
PostgreSQL concurrent-worker stress, process-kill fault injection, adapter
certification, migration rollout, and end-to-end broker reconciliation remain
required before any live route can be enabled.
