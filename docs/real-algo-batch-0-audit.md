# Real Algo — Batch 0 Read-Only Audit

**Audit base:** `16711dd71bb0f372b0666e0898491791eefff0b4`  
**Scope:** Box Spread real-algo preparation only. This document is an audit record, not an implementation enablement.

## Safety boundary

- No production server, service, environment, process, database, WebSocket, or live scanner was changed by this audit.
- No deployment or restart was initiated.
- No broker order routing was enabled; Angel One adapter still raises `NotImplementedError` for place/cancel.
- Box Spread paper trading remains a dedicated route and states it never submits broker orders.
- Backtesting, Cash-Future, Synthetic Future, and Calendar Spread are out of scope. Backtesting code and tests must remain unchanged unless separately and explicitly authorized.

## Read-only findings

### Runtime and market data

- `backend/app/main.py` imports and registers the broker routes alongside existing scanner routes. It owns the FastAPI lifespan and supervises existing live runner tasks.
- `/ws/dashboard` publishes snapshots from existing process-local scanners; it explicitly documents that the browser does not open a broker socket.
- `backend/app/market_data/common_strategy_feed.py` provides a process-wide shared feed manager. Strategy consumers register subscriptions against the common manager; strategies should not independently open broker WebSockets.
- Box Spread has its own live opportunity WebSocket route and separate paper-trading router.
- **Implementation constraint:** Do not add a new startup task or edit `main.py` in early batches. Do not open a second market-data socket. Any future market-data integration must be separately reviewed and must consume existing data without changing current subscriptions or callback behaviour.

### Broker integration and security blockers

- `backend/app/brokers/registry.py` currently registers only `angel_one`; FYERS and Zerodha adapters are not registered.
- `backend/app/brokers/routes.py` currently accepts only Angel One-shaped credentials and rejects other broker names.
- The broker connection metadata and safety state are process-local in-memory stores.
- The current `current_user_id` dependency selects the first active user (and can bootstrap a system identity); it is not sufficient owner authentication for live trading.
- `/safety/enable` only arms an in-memory state and explicitly returns `live_order_routing: false`. This must not be represented as live execution.
- **Blocker:** No real order routing until authenticated owner authorization, durable single-account ownership, session lifecycle, order reconciliation, risk controls, and failure recovery are designed and tested.

### Verification baseline

- The repository's `.github/workflows/build.yml` defines dependency install, Python compilation, and pytest, but its current trigger is `workflow_dispatch` only.
- A fresh user-triggered pytest run was attempted on branch `fix/daily-shard-test-syntax` (PR #292). The syntax/collection blocker and dashboard test path were corrected in test-only commits; no production runtime code was changed by those fixes.
- The subsequent run collected 2,740 tests but stopped because `pytest -x` encountered a pre-existing failure after 35 passing tests: `app/backtesting/tests/test_durable_replay.py::test_durable_replay_journals_events_decisions_and_checkpoint`. Actual records include `ORDER_LIFECYCLE` and `FILL`, while that test's expected list omits them. The same stale expectation exists on `main`, and another test in the same file explicitly checks lifecycle/fill records.
- Per the project owner's instruction, **do not modify or otherwise work on backtesting**. The failing test was left unchanged. Since `-x` stopped the run, this is not a full-suite pass and remaining tests are unverified.
- **Baseline status: BLOCKED / NOT GREEN.** Do not claim a passing full-suite baseline. Batch 1 must not start until an acceptable baseline is established without changing out-of-scope backtesting, or the project owner explicitly changes the scope.
- Existing targeted backend, integration, and common-feed regression checks recorded for PR #290 are not a substitute for a fresh full-suite result.
- The current audit base commit is `16711dd71bb0f372b0666e0898491791eefff0b4`, which aligns Box Spread WebSocket freshness to individual option-leg timestamps.

## Batch 0 exit checklist

- [x] Identify runtime entry point and lifespan ownership.
- [x] Identify shared WebSocket/feed fan-out boundary.
- [x] Identify Box Spread live-feed and paper-route separation.
- [x] Identify broker integration and authentication blockers.
- [x] Define out-of-scope strategies and deployment restrictions.
- [x] Attempt baseline verification and document the exact existing blocker and test-run limits.
- [x] Record the read-only audit findings for review.
- [ ] Obtain a green, fresh full-suite baseline without touching out-of-scope backtesting.
- [ ] Owner review/sign-off before any runtime code change.

## Batch 0 disposition

**The read-only audit deliverable is complete. The implementation gate is still blocked by the baseline failure above.** This distinction is intentional: finishing Batch 0 documentation does not authorize Batch 1 or live execution.

## Next batch gate

Batch 1 may begin only after the baseline gate is resolved and the owner reviews the findings. It must use a dedicated feature branch and isolated broker/security modules. Do not change `backend/app/main.py`, the common feed manager, existing scanner runners, deployment workflows, or paper-trading routes without a demonstrated dependency and separate review. Backtesting, Cash-Future, Synthetic Future, and Calendar Spread remain out of scope. Live order routing stays OFF.
