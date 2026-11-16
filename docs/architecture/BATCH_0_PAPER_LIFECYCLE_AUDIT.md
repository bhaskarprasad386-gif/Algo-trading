# Batch 0 — Box Paper Lifecycle and Crash-Boundary Audit

**Status:** Source/test audit completed; durable Box Paper lifecycle audit events are implemented on the Batch 0 follow-up branch and awaiting CI.  
**Audited main commit:** `8da655f0c287251cf1602cae484714a7c26fcf15`.

## 1. Production caller inventory for the generic execution package

GitHub repository-wide code search was run for the core generic execution symbols. The search results were:

| Symbol | Production paths found | Test paths found | Finding |
|---|---:|---:|---|
| `DualExecutionEngine` | `backend/app/execution/dual_engine.py` only | `test_dual_execution.py`, `test_dual_engine_live_gate.py` | No production orchestration caller found |
| `IdempotencyGuard` | `idempotency.py`, `dual_engine.py` | `test_idempotency.py`, `test_dual_engine_live_gate.py` | In-memory utility, not wired into API order flow |
| `evaluate_watchdog` | `multileg.py` only | `test_multileg.py` | Decision helper, no broker submit/cancel/reconcile caller found |
| `ConfirmationGateway` | `confirmation.py`, `dual_engine.py` | generic execution tests | In-memory confirmation helper |
| `SafetyController` | `safety.py`, `dual_engine.py` | generic execution tests | In-memory safety helper |
| `FillAccounting` | `fill_accounting.py` only | `test_fill_accounting.py` | No production caller found |

The Box Paper route directly uses its own `BoxSpreadPaperEngine` and `BoxSpreadPaperLedger`; it does not use `DualExecutionEngine`. This confirms the design direction: integrate through a carefully staged adapter around existing Box Paper behavior and evolve the existing generic package, rather than creating a third execution stack.

*Scope limitation:* GitHub code search is an exact-symbol search over indexed repository code. It does not prove the absence of dynamic imports, reflection, string-based dispatch, or external deployment code. Runtime call tracing is still required before removal or replacement.

## 2. Current Box Paper entry lifecycle

The inspected route performs the following ordered steps:

1. Acquire an in-process lock keyed by `(account_key, trade_id)`.
2. Require the Paper execution switch to be ON.
3. Load the persisted trade request.
4. Reject if an entry claim already exists; separately reject if durable fills already exist.
5. Resolve contracts and find a matching Box scanner opportunity no older than five seconds.
6. Read fresh four-leg shared market depth; require depth for the requested complete lot count.
7. Validate all four limit prices/marketability, immutable original alert edge, and effective entry edge.
8. Recheck the Paper switch immediately before creating the durable claim.
9. Create a unique durable entry claim; the DB uniqueness boundary serializes duplicate attempts across API workers.
10. Create/execute the in-memory simulator intent and calculate all four fills.
11. Persist the four fills through one `record_entry_basket` DB transaction.
12. That transaction validates roles, direction, equal quantities, original trade quantity, balance, and claim; writes all four ledger rows, updates virtual balance, marks the trade `FILLED`, and marks the claim `COMPLETED`.

Important: the atomicity guarantee in step 11 is a database transaction for Paper ledger writes. It is not an exchange-level atomic fill guarantee and must not be generalized to Live orders.

## 3. Entry crash-boundary matrix

| Crash/failure point | Durable state possible | Current behavior / safety | Target action |
|---|---|---|---|
| Before durable claim | No claim and no fills | Request can be retried after fresh quote/depth revalidation | Safe retry with same immutable intent/idempotency key |
| After claim, before simulator intent | Claim remains `CLAIMED`; no fills | Retry is blocked; recovery marks manual review; no automatic replay | Persist normalized intent before claim/submission and classify as definitely-not-submitted only when provable |
| During in-memory simulation | Claim remains `CLAIMED`; no ledger fills | Simulator is process-local; restart loses its progress; retry blocked | Simulator must emit durable execution attempt and deterministic event IDs |
| After simulation, before ledger transaction | Claim remains `CLAIMED`; no ledger fills | Simulated result is lost on process death; no blind replay | Persist simulator result/intent before commit, or use one transaction/outbox boundary |
| During four-leg ledger transaction | Transaction commits all four rows or none | DB transaction prevents a half-written four-leg basket | Keep atomic ledger invariant and add crash-injection tests |
| After ledger transaction commit, before HTTP response | Four fills durable; trade `FILLED`; claim `COMPLETED` | Client retry is blocked at the existing-claim check, not receipt-replayed by entry endpoint | Add an authenticated read/replay endpoint keyed by immutable request ID; never resimulate |
| After response | Four fills durable | Ledger/history are source of truth | Normal read-only history/recovery |

### Critical nuance

The current entry API intentionally does not automatically replay an ambiguous claim. This is fail-safe against duplicate simulation, but a retry after a successful DB commit and lost HTTP response currently receives a conflict instead of an idempotent success receipt. The ledger contains all four durable receipts, so the future normalized API should safely return those original receipts without executing again. This is a usability/reliability gap, not a reason to loosen the current replay guard.

## 4. Current Box Paper exit lifecycle

The exit-preview route is read-only and derives executable prices from fresh server-side four-leg depth. It can show a partial complete-lot preview, but it does not mutate the ledger.

The exit execution route:
1. Validates order type and exact four-leg limit-price shape.
2. Computes a deterministic digest from the request `fill_id`.
3. Checks for an existing durable basket receipt before resolving instruments or reading current quotes.
4. If found, requires exactly four prior fills, all four unique roles, and identical quantities; then returns the original durable receipts and current trade summary.
5. Otherwise resolves the original contract set and open position.
6. Reads fresh two-sided depth and selects the largest complete lot quantity supported across all four legs.
7. Builds one four-leg basket and calls `record_exit_basket` once.
8. The ledger transaction checks equal quantities and that the four legs match the currently open quantities; writes all four close entries and cash/charge changes in one transaction.

### Exit crash-boundary matrix

| Crash/failure point | Durable state possible | Current behavior / safety |
|---|---|---|
| Before any DB write | No exit fills | Retry may re-read fresh depth and attempt exit |
| During ledger transaction | All four exit rows and balance updates commit, or none | Atomic Paper ledger transaction |
| After commit, before HTTP response | Four durable exit fills exist | Same request `fill_id` finds and replays the original four receipts before market-data resolution |
| Retry with same fill ID but incomplete/mismatched prior rows | Corrupt/legacy partial receipt set | Returns conflict/manual review; no new exit |
| Retry with a new fill ID after previous exit | New request | Remaining open quantity is recalculated; only complete lots supported by all legs can close |

The exit path has stronger request-level receipt replay than the entry path. Preserve this asymmetry only until entry gains a tested durable replay endpoint.

## 5. Expiry settlement and recovery

- The expiry-settlement endpoint is deliberately disabled with HTTP 503 because caller-supplied leg prices are not authenticated official settlement data. Re-enable only after a trusted server-side settlement source and intrinsic-value derivation from persisted strikes/option types are verified.
- The recovery endpoint is read-only, reports `automatic_replay=false`, and lists durable non-terminal trades.
- A claimed entry with a partial fill is surfaced with `manual_review_required=true`; the restart regression test proves a new ledger instance cannot claim/replay it and that route replay is blocked before contract resolution or quote reads.
- A fresh Paper engine instance loses process-local intent/execution state; durable ledger and claim state remain available.

## 6. Tests found in the repository

The inspected suite includes:
- `test_box_spread_paper_engine.py`: intent validation, idempotency, adverse slippage, limit validation, partial fills, matched quantities, residual exposure, duplicate fill IDs, and overfill protection.
- `test_box_spread_paper_ledger.py`: durable account/trade/fill/charge/cash-ledger invariants, atomic basket writes, and duplicate handling.
- `test_box_spread_paper_routes.py`: route-level create/entry/exit/recovery behavior.
- `test_box_spread_paper_restart_safety.py`: process-restart simulation with partial durable entry fill; duplicate claim/replay blocked and manual review required.
- `test_box_spread_paper_schema_startup.py`: schema startup compatibility.

These are source-level test inventory findings. No fresh test suite was executed in a local runtime during this audit; CI status is tracked separately on PR #504.

## 7. Gaps to close before unified Paper cutover

1. Add entry receipt replay after a successful commit / lost HTTP response, without re-running quote reads or simulation.
2. Add deterministic crash-injection tests for each row in both crash matrices (before claim, after claim, after simulation, during commit, after commit/before response).
3. Persist the simulator attempt/result in a durable normalized intent/event record before considering it recoverable.
4. Add concurrent multi-worker tests for duplicate entry claims and concurrent exits against the same open basket on PostgreSQL (SQLite-only tests are not sufficient for production locking semantics).
5. Add explicit state transition/audit events for `CLAIMED -> COMPLETED`, failed pre-claim validation, manual-review decisions, and operator recovery.
6. Ensure any recovery action is authorized to the owner and account; caller-provided `account_key` must not become the future account authorization primitive.
7. Preserve expiry settlement as disabled until trusted settlement verification is complete.

## 8. Decision

The current Box Paper ledger has robust atomic four-leg persistence and conservative restart replay protection. It is not yet a durable unified execution lifecycle: simulator state is in memory, entry response replay is incomplete, and ambiguous claims require manual review. Keep the existing behavior unchanged while the durable intent/event layer and PostgreSQL concurrency tests are built.

**The lifecycle event implementation changes only the Box Paper ledger schema and authenticated audit endpoints. Scanner, WebSocket subscriptions, broker adapters, deployment, and Live Trading settings are unchanged.**


## 7. Durable lifecycle audit events (Batch 0 follow-up)

The Box Paper ledger now has an append-only `box_spread_paper_audit_events` table, created through the existing SQLAlchemy startup metadata path. Events are written in the same database transaction as the corresponding durable claim or four-leg entry commit:

- `ENTRY_VALIDATION_FAILED`: a pre-claim HTTP 409/422 validation rejection is recorded without changing trade or claim state.
- `ENTRY_CLAIMED`: the one-shot claim is created; records prior trade state and `CLAIMED`.
- `ENTRY_COMPLETED`: all four entry legs and the completed claim are committed; the event is part of that same transaction.
- `MANUAL_REVIEW_REQUIRED`: a post-claim execution failure is recorded without clearing the claim or enabling retry.
- `MANUAL_REVIEW_DECISION`: an explicit ledger-level decision is appended with actor and reason; it does not alter execution state or trigger replay.

Events are read in stable insertion order through the ledger's account-scoped read method. Event details are diagnostic only and do not authorize broker or Paper execution. The event table is append-only by application contract; database-level immutability enforcement and an authenticated operator-facing recovery workflow remain separate follow-up work.

Pre-claim HTTP 409/422 validation failures are captured by the entry route. The authenticated paper-owner subject is recorded for manual decisions; account-key authorization remains a separate ownership-hardening task. No Live broker path, scanner, WebSocket, or deployment behavior is changed by this work.
