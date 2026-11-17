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

The review is bounded to the requested limit (maximum 1,000). If more events
exist than the requested limit, it returns `INCOMPLETE` with `truncated=true`
rather than falsely claiming the account is clear. The review never replays
callbacks, writes fills, changes intent state, polls a provider, or authorizes
order actions. Results are limited to the authenticated
single broker account, and `trading_authorized` remains false. The endpoint
returns only event identity and quantity/timestamp diagnostics, not callback
payload JSON or average fill prices.


## Operator-requested deferred callback replay eligibility (dry-run only)

The owner-authenticated `POST /api/v1/brokers/events/replay-eligibility`
endpoint accepts an explicit list of persisted callback event IDs and the
confirmation phrase `REVIEW DEFERRED FILL REPLAY ELIGIBILITY`. It is a
read-only eligibility calculation, not a replay endpoint.

The review is scoped to the single broker/account bound to the authenticated
owner session. Every selected event must exist in that scope and have a
trade identity, positive quantity, execution price, and trustworthy timestamp.
The broker order must map exactly to a persisted intent leg. Existing fill IDs,
quantity overruns, cumulative sequence gaps/conflicts, incomplete leg-to-order
identity, and unequal projected quantities across the intent's legs block
eligibility. The operator must select a complete event set whose projected
per-leg fill quantities balance; the review never infers missing fills.

A selected set containing the same broker fill ID more than once is blocked,
even when its projected per-leg totals would otherwise balance. Duplicate
callback IDs cannot be counted as separate executions or used to inflate a
projected quantity; an exact duplicate callback outside the selected set may
remain harmless when it matches the selected execution evidence exactly.

An eligible response means only that the selected evidence passed these
preconditions for a separately designed operator workflow. It does not replay
callbacks, write fill rows, mutate intent/leg state, poll a broker, submit,
cancel, modify, retry, adopt, or resume orders. The response always includes
`replay_performed=false`, `fill_ledger_mutated=false`,
`intent_state_mutated=false`, and `trading_authorized=false`.


## Replay eligibility audit receipts and expiry

Each owner-authenticated eligibility review now writes an append-only audit
receipt scoped to the currently bound broker/account. The receipt records the
explicit event-ID set, a SHA-256 digest of the reviewed evidence/result, the
result status, and review timestamps; callback payloads and credentials are
not copied into the audit row.

A review receipt is current for 60 seconds only. The account-scoped
`GET /api/v1/brokers/events/replay-eligibility/audit/{review_id}` endpoint
reports `CURRENT` or `EXPIRED`. Expiry never grants or implies approval.
There is deliberately no approval, replay, or execution endpoint in this
batch: `approval_granted=false`, `replay_authorized=false`, and
`trading_authorized=false` remain explicit. A future operator workflow must
perform a fresh eligibility review and independently revalidate evidence;
this receipt is an audit record, not a reusable authorization token.
