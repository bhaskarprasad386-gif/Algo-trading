# Batch 0 — Broker Order Status Normalization

## Purpose

Normalize provider status text into one conservative shared state without
turning uncertain evidence into permission to retry or execute.

Supported normalized states:
- `OPEN`
- `PARTIALLY_FILLED`
- `PARTIALLY_FILLED_CANCELLED`
- `FILLED`
- `CANCELLED`
- `REJECTED`
- `UNKNOWN`

## Safety behavior

- Supports the five named broker identities only.
- Unknown/numeric vendor codes remain `UNKNOWN`; numeric mappings are not guessed.
- Filled quantity is preserved even when the broker reports cancellation or rejection.
- A partial fill followed by cancellation is represented as a distinct terminal
  state and always requires reconciliation.
- A filled quantity that conflicts with OPEN/CANCELLED/REJECTED/UNKNOWN provider
  status is preserved as FILLED but marked as a conflict requiring reconciliation.
- A provider-reported FILLED status with no visible fill quantity requires
  reconciliation; it is not treated as proof of a persisted fill.
- Quantity validation rejects booleans, non-integers, zero/negative order sizes,
  and fills greater than order quantity.

## Deliberate limits

This module is a pure evidence classifier. It does not call providers, write the
ledger, cancel/retry orders, resolve UNKNOWN outcomes, or resume execution.
Provider-specific numeric status maps must be added only after verifying the
provider's current official API contract and fixture evidence. No live order
mutations are enabled by this change.
