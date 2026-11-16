# Batch 0 — Broker Order Status Normalization

## Purpose

Normalize provider status text into one conservative shared state without
turning uncertain evidence into permission to retry or execute.

Supported normalized states:
- `OPEN`
- `PARTIALLY_FILLED`
- `PARTIALLY_FILLED_CANCELLED`
- `PARTIALLY_FILLED_EXPIRED`
- `FILLED`
- `CANCELLED`
- `EXPIRED`
- `REJECTED`
- `UNKNOWN`

## Safety behavior

- Supports the five named broker identities only.
- Unknown numeric vendor codes remain `UNKNOWN`. FYERS API v3 numeric codes are mapped only for FYERS: `1` cancelled, `2` traded/filled, `4` transit/open, `5` rejected, `6` pending/open, `7` expired; `3` and unrecognized codes remain `UNKNOWN`. These codes are never applied to the other four brokers.
- Filled quantity is preserved even when the broker reports cancellation or rejection.
- A partial fill followed by cancellation or expiry is represented as a distinct terminal
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


## Durable reconciliation integration

The provider history adapters now preserve the raw textual order status in each
order snapshot for Angel One, Zerodha, FYERS, Shoonya and m.Stock. The account-
scoped reconciliation service stores both the raw status and its normalized
classification in the hash-protected report payload.

- Unknown or missing provider statuses produce
  `ORDER_STATUS_REQUIRES_RECONCILIATION`.
- Conflicting terminal status/fill evidence produces
  `ORDER_STATUS_CONFLICT`.
- Partial-fill-then-cancel evidence remains a terminal partial-fill state and
  is explicitly blocked for manual reconciliation.
- Raw status, normalized state, requested quantity, filled quantity, remaining
  quantity, terminal flag and conflict flags are included in durable evidence.
- The reconciliation identity hash covers this status evidence, so replaying
  the same ID with changed status evidence is rejected as an identity conflict.

This is still read-only evidence handling. It does not cancel, retry, modify,
submit, or resume orders. Provider-specific status aliases remain conservative; the snapshot-to-event polling path
uses this same normalizer. A provider-reported FILLED/PARTIAL status without cumulative
fill quantity remains UNKNOWN in the event ledger until tradebook evidence arrives.
Unknown values stay blocked until verified fixtures support an explicit mapping.


## Multi-leg spread fill completeness

Account reconciliation also compares broker-confirmed cumulative filled
quantities across every persisted leg in each multi-leg intent. If one leg has
filled 25 units while another has filled 0 (or any other unequal quantity), the
durable report includes `MULTI_LEG_FILL_QUANTITY_MISMATCH` and remains
inconsistent. Equal partial quantities are not classified as a leg imbalance,
but the existing per-order tradebook and status checks still apply.

This check is read-only and uses broker order snapshot cumulative quantities;
it does not infer fills from local estimates, alter the immutable fill ledger,
submit compensating orders, or authorize execution resume.


The same multi-leg quantity-balance guard is applied during read-only restart
recovery review. A spread with different broker-confirmed cumulative fill
quantities across persisted legs is classified as blocked for operator
reconciliation; equal partial quantities across all known legs are not
misclassified as an imbalance. This closes the gap between the standalone
reconciliation report and the recovery-review path.


## Out-of-order callback fill evidence

Trade-identified callback events are stored immutably before attempting fill
progress. If a callback's cumulative filled quantity is ahead of the currently
persisted tradebook/callback fills, the callback is retained as
`CUMULATIVE_FILL_GAP` evidence and no fill ledger or intent progress is
changed. Replaying that exact callback after its missing predecessor arrives
re-attempts application. Exact broker trade IDs remain idempotent across
different callback IDs; changed execution data under an existing trade ID is
a conflict.

A cumulative quantity below already persisted fills, or a cumulative quantity
above the registered order size, remains a hard fail-closed error. Deferred
events do not trigger polling, retry, order mutation, automatic adoption, or
execution resume. Reconciliation remains the authority for unresolved gaps.


## Deferred callback event visibility

The owner-authenticated `GET /api/v1/brokers/events/review` endpoint exposes a
bounded, account- and broker-scoped read-only view of callback events whose
fill evidence is not yet reconciled. It distinguishes cumulative fill gaps,
conflicting cumulative quantities, missing trade identity, unresolved broker
order identity, and fill application that has not reached the immutable fill
ledger. Exact fill evidence already present in the ledger is omitted from the
unresolved list.

The review never replays callbacks, writes fills, changes intent state, polls a
provider, or authorizes order actions. Results are limited to the authenticated
single broker account, and `trading_authorized` remains false. The endpoint
returns only event identity and quantity/timestamp diagnostics, not callback
payload JSON or average fill prices.
