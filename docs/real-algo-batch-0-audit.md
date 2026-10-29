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

### Verification scope and status

- **Batch 0 is a read-only audit. Its deliverable is complete.** Backtesting is explicitly out of scope and must not be edited, repaired, or treated as a Box Spread audit defect.
- The earlier full-suite run used `pytest -x`, collected 2,740 tests, passed 35, then stopped at `app/backtesting/tests/test_durable_replay.py::test_durable_replay_journals_events_decisions_and_checkpoint`. The assertion expects no `ORDER_LIFECYCLE` or `FILL` records although the actual replay emits them. This is an out-of-scope test result, not evidence that the Box Spread audit failed.
- Because `-x` stopped at that point, tests after the failure remain unverified. We must not call the entire repository test suite green.
- The repository's `.github/workflows/ci-tests.yml` runs the narrower `backend/tests` suite and excludes several named integration/E2E tests. It is not a full run of all `backend/app` tests. A passing result from that workflow should be reported only as scoped backend CI, not a full-suite pass.
- The repository's `.github/workflows/build.yml` runs compilation and `pytest -x -vv`, and its trigger is `workflow_dispatch` only.
- Earlier syntax and dashboard test-path fixes were test-only changes on PR #292; no production runtime code was changed by those fixes.
- **Scope decision:** Do not change Backtesting or alter tests/workflows to hide its failure. For Batch 0, record the Backtesting result as excluded/out-of-scope and assess Box Spread/non-Backtesting checks separately. A full-repository green claim remains unavailable until a non-invasive, appropriately scoped verification can actually establish it.
- On the documentation update commit `db1173949bddb26639d1b5d6e443c40094593a02`, GitHub Actions reported **Integration Tests: success** and **Common Feed Targeted Regression: success** (run `40816144328`). These are scoped green checks, not a full non-Backtesting suite result.
- On audit-report commit `d0e53444ceec42273960c3226bc4fdeaa3c796bd`, **Integration Tests** run `40816274377` completed successfully, including the **Common Feed Targeted Regression** job. **Backend Tests** run `40816274408` completed with 2,254 passed, 1 failed, and 1 xpassed. The sole failure was `backend/tests/test_live_synthetic_stream.py::test_stream_persists_previous_second_when_bucket_advances_and_flushes_final_bucket` (`assert 1 == 2`). Synthetic Future is explicitly out of scope, so this is not a Box Spread failure and Synthetic Future code/tests must not be changed under this scope. Nevertheless, the Backend Tests workflow is red and must not be reported as green.
- The current audit base commit is `16711dd71bb0f372b0666e0898491791eefff0b4`, which aligns Box Spread WebSocket freshness to individual option-leg timestamps.

## Batch 0 exit checklist

- [x] Identify runtime entry point and lifespan ownership.
- [x] Identify shared WebSocket/feed fan-out boundary.
- [x] Identify Box Spread live-feed and paper-route separation.
- [x] Identify broker integration and authentication blockers.
- [x] Define out-of-scope strategies and deployment restrictions.
- [x] Attempt baseline verification and document the exact existing blocker and test-run limits.
- [x] Record the read-only audit findings for review.
- [x] Complete and record the read-only Batch 0 audit deliverable.

**Separate implementation gates (not audit-documentation tasks):**
- [x] Run and record scoped non-Backtesting checks: Integration Tests and Common Feed Targeted Regression passed; Backend Tests has one Synthetic Future out-of-scope failure. Do not modify Backtesting or Synthetic Future to obtain a green result.
- [ ] Owner review/sign-off before any runtime code change.
- [ ] Keep full-repository test status explicitly unverified unless a complete non-invasive run actually finishes.

## Batch 0 disposition

**BATCH 0 AUDIT: COMPLETE.** The Backtesting failure is explicitly classified as out-of-scope, not a Batch 0 Box Spread audit failure, and Backtesting remains untouched. Relevant non-Backtesting verification and owner review are separate gates. The full repository test suite is **not verified green**; do not claim otherwise. Live order routing stays OFF.

## Next batch gate

Batch 1 may begin only after relevant non-Backtesting checks are run and recorded, the owner reviews the findings, and any required implementation gates are explicitly cleared. It must use a dedicated feature branch and isolated broker/security modules. Do not change `backend/app/main.py`, the common feed manager, existing scanner runners, deployment workflows, or paper-trading routes without a demonstrated dependency and separate review. Backtesting, Cash-Future, Synthetic Future, and Calendar Spread remain out of scope. Live order routing stays OFF.
