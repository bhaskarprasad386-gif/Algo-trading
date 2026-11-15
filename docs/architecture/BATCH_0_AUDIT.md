# Batch 0 — Architecture Audit and Regression Baseline

**Status:** In progress — documentation baseline only; no runtime code changed.  
**Audit base:** `main` at `8da655f0c287251cf1602cae484714a7c26fcf15`  
**Audit date:** 2026-11-15

## Scope and non-regression constraints

This audit prepares the repository for a unified Paper/Live execution engine, multiple broker accounts per user, VPS/Cloudflare hosting, and a lightweight Android client whose normal pages can update remotely.

The following existing systems are protected during subsequent work:

- Box Spread live scanner, contract resolution, shared market-data subscriptions, and existing broker WebSocket infrastructure.
- IV scanner and IV Skew Flip runtime lifecycle.
- Existing shared page-push WebSocket and its page channel contracts.
- Existing Paper Trading APIs, fee ledger, trade history, and recovery endpoints.
- Existing Butterfly/Condor weekly and monthly pages and their Box scanner-fed calculations.

No scanner strategy or market-data subscription changes are in scope for Batch 0.

## Repository baseline

- Main HEAD: `8da655f0c287251cf1602cae484714a7c26fcf15`.
- Latest Backend Tests workflow run for this commit: successful.
- Combined commit status also reports a Vercel failure with a build-rate-limit target URL. This is separate from the Backend Tests result.
- Main branch metadata currently reports branch protection disabled.
- CI uses Python 3.11 and provisions PostgreSQL 16, but its primary test command sets `DATABASE_URL=sqlite:///:memory:`; a dedicated PostgreSQL URL is provided for selected tests. The test command also explicitly excludes several integration/historical suites. Therefore a green Backend Tests run is not a complete PostgreSQL or production-runtime certification.

## Findings

### B0-01 — Execution package exists but is not the production order lifecycle

Existing modules under `backend/app/execution/` include:

- `dual_engine.py`
- `multileg.py`
- `risk.py`
- `idempotency.py`
- `fill_accounting.py`
- `safety.py`
- `retry.py`
- `confirmation.py`

The dual engine currently delegates to a generic callable fill executor and maintains small in-memory paper/live state objects. Repository search found its direct references concentrated in tests rather than a production order orchestration path. The idempotency guard and confirmation gateway are also process-memory implementations. Multi-leg watchdog logic returns decisions; it does not itself submit, reconcile, cancel, or recover real broker orders.

**Required direction:** evolve this existing package; do not create a duplicate execution engine. Define a durable normalized order lifecycle and adapter contracts before routing any production paper or live flow through it.

### B0-02 — Box Spread Paper Trading has a separate simulation engine and durable ledger

The current Paper route uses:

- `BoxSpreadPaperEngine` for in-memory intents and simulated fills.
- `BoxSpreadPaperLedger` for SQL-backed accounts, trades, entry claims, fills, charges, and balances.

Positive existing safeguards:

- Fresh Box scanner opportunity and fresh four-leg depth are checked server-side.
- Browser-supplied reference prices are not trusted.
- A durable entry claim is created before simulation.
- The ledger atomically persists all four equal-quantity entry fills or none.
- Stable fill IDs and idempotency checks block duplicate writes.
- Recovery endpoint is read-only and does not replay fills automatically.
- Paper execution is explicitly `PAPER_ONLY`; broker order routing is disabled.
- The paper ON/OFF switch resets to OFF on API process restart.

Remaining gap: simulated execution state is process-local while ledger state is durable. A crash between simulation and ledger commit leaves an intentionally ambiguous claimed entry that requires manual review. This is safe against blind replay, but it is not yet the unified durable Paper/Live order state machine.

### B0-03 — Paper account key is not an authenticated trading-account identity

Paper ledger accounts are keyed by a caller-provided `account_key` under a single configured Paper owner token. This provides separate virtual ledgers but does not establish a broker account identity or per-user account authorization boundary.

Do not reuse `account_key` as the future live account ID. Introduce a proper account entity and explicit ownership checks.

### B0-04 — Broker connections are single-account-per-broker and process-local

Current broker implementation:

- Registry registers only `AngelOneAdapter`.
- Connect route rejects brokers other than `angel_one`.
- Session and connection stores are keyed by `(user_id, broker)`, so a second account for the same broker cannot coexist independently.
- Adapter instances and connection metadata are process-local.
- Broker protocol is minimal: connect, place, cancel, disconnect; normalized order updates, modifications, funds, positions, and reconciliation are not fully specified.
- Existing durable Box Spread owner state binds one account fingerprint and is deliberately fail-closed.
- Real-trading enable endpoint returns HTTP 503 until safety/reconciliation prerequisites exist.

The live route must remain fail-closed during migration. The single-owner model must not be relaxed by simply changing its unique key.

### B0-05 — Database models are not yet account-scoped for multi-broker live trading

- `TradingAccount.user_id` is unique, allowing one trading-account row per user.
- Generic `Order` and `Position` records carry user IDs but no broker-account foreign key.
- Box Paper ledger has its own account-key model, separate from generic `TradingAccount`.

A migration needs explicit account ownership, account-scoped order uniqueness, positions, risk limits, broker session metadata, and audit trails. Existing Paper records must remain readable and attributable during migration.

### B0-06 — Startup and shared page-push constraints

- `main.py` runs schema migrations and `Base.metadata.create_all` during module import.
- Lifespan recovery marks the persisted single-owner broker connection disconnected after restart; it does not restore broker sessions.
- Shared page-push is a bounded, process-local fanout with explicit page channels and sequence numbers.
- Butterfly/Condor page updates are derived from existing Box scanner callbacks; the page-push layer itself owns no market-data socket or subscription.
- Current in-process page push requires a single-worker deployment assumption unless a shared event transport is introduced.

Future execution events should use a separate namespace and must not add market-data subscriptions or change scanner callback timing. If/when the API is run with multiple workers, page and execution event fanout must be moved to a shared transport with sequence/resync semantics.

### B0-07 — Android is native Kotlin but not yet server-driven

Current Android project is a native Kotlin app using OkHttp, Retrofit, and coroutines. The default backend URL in Gradle still points to the legacy Render backend. Box Spread screen polls every five seconds. Broker UI models are Angel One-specific.

A future hybrid architecture should retain a small native shell for auth, secure storage, notifications, and safety-critical native capabilities, while loading versioned remote page manifests/content. Routine page/layout changes must not require APK updates; new native permissions or SDK functionality still will. Existing pages must continue working during rollout, with cached last-known-good UI fallback.

### B0-09 — Android owner token is memory-only; native session persistence is not implemented

The Android `ApiService` stores the real-owner access token in a process-local `@Volatile` property. It is not restored from encrypted storage after process death. The Retrofit client injects the token for broker API requests and clears it on HTTP 401. No Android WebSocket client was found in the inspected API service. The future mobile account/session layer therefore needs secure token storage and refresh/re-auth design, plus a separate lifecycle-managed push client. Broker passwords, API secrets, and TOTP seeds must never be persisted in ordinary preferences.

### B0-10 — VPS deployment run failed after SSH preflight; remote state remains unknown

The latest VPS Deploy run for the audited main commit completed with failure. The SSH key scan and authentication preflight succeeded and printed `SSH_AUTH_PREFLIGHT_OK`. The subsequent synchronous SSH deployment session later reported `Timeout, server not responding` and `REMOTE_DEPLOY_FAILED_OR_SSH_DISCONNECTED`. The logs do not show a `REMOTE_DEPLOY_SCRIPT_STARTED` marker or deployment progress, so they do not establish whether the remote script began or whether the VPS service changed state. This is an ambiguous deployment outcome, not proof that the service is down.

The current bootstrap performs OS package updates, Python dependency installation, service restart, and up to 180 seconds of local health retries. Before any retry, inspect the VPS service, current Git HEAD, deployment marker, systemd journal, disk/memory, and API health. Future deployment automation should use a durable remote job/lock/log/status record so an SSH transport timeout can be reconciled without blindly launching a second deployment.

### B0-08 — Hosting migration is not ready to cut over

The repository has a VPS backend deployment workflow, but this audit has not yet verified a production-ready frontend deployment service on the VPS/Cloudflare path. Vercel must remain available until frontend, API, WebSocket, TLS, auth, and Android end-to-end verification passes.

## Target contracts to design before implementation

1. **TradingAccount:** stable account ID, user owner, broker, broker-side account identifier/fingerprint, display label, lifecycle state, encrypted credential/session reference, and per-account mode/safety settings.
2. **BrokerAdapter:** typed auth/session lifecycle, place/modify/cancel, order status, order updates, positions, funds/margin, and reconciliation; adapters must be isolated per account.
3. **ExecutionIntent:** immutable strategy/signal version, mode, account ID, instrument/legs, quantities, price constraints, risk snapshot, idempotency key, and audit metadata.
4. **Order lifecycle:** durable states for validation, risk approval, submission, acknowledgement, partial fill, fill, cancel pending, cancelled, rejected, unknown, reconciliation required, and terminal completion.
5. **Multi-leg coordinator:** coordinated submission and per-leg fill tracking, bounded cancellation, residual exposure management, and explicit recovery. Never promise exchange-level atomicity for four independent live option orders.
6. **Risk engine:** account-level and user-level caps, daily loss, open exposure, available margin, stale-data rejection, order throttles, and kill switch.
7. **Paper adapter:** same normalized lifecycle and risk path as live, but simulated fills and no broker side effects.
8. **Live adapters:** broker-specific transport only after durable order lifecycle, reconciliation, and account safety gates are proven.
9. **Android remote UI:** signed/versioned manifest, capability allowlist, WebSocket update notification, ETag/cache invalidation, rollback, and last-known-good fallback.
10. **Deployment:** VPS frontend/API split, Cloudflare edge protection, shared event transport where needed, health checks, rollback, and a verified Vercel-to-VPS cutover plan.

## Batch 0 regression gate

Before feature implementation, establish tests for:

- Box Spread scanner API response and opportunity freshness contract unchanged.
- Box/IV/Butterfly-Condor WebSocket channels, sequence numbers, and resync unchanged.
- Existing shared market-data socket count/subscriptions unchanged.
- Paper create, entry claim, four-leg fill, exit, fee ledger, and restart recovery unchanged.
- Paper mode never calls a broker adapter.
- Broker session/account isolation and fail-closed live route unchanged.
- Execution duplicate request/fill handling and partial multi-leg recovery.
- Android API compatibility and remote-page fallback.
- Backend tests plus dedicated PostgreSQL integration tests.
- VPS deployment health checks and Vercel rollback availability.

## Batch 0 remaining work

- Complete full startup/lifespan dependency map and enumerate all runtime callers of execution modules.
- Complete Paper route-to-ledger state transition diagram, including exit/settlement and every crash boundary.
- Inspect every broker adapter and broker WebSocket owner to avoid conflating trading sessions with market-data sessions.
- Capture baseline scanner latency, API latency, event-loop lag, worker count, and socket/subscription counts from a safe runtime snapshot.
- Inspect Android navigation, auth token storage, WebSocket client, lifecycle, and release workflows.
- Target logical ERD, order-state diagram, adapter contract, expand-and-contract migration/rollback plan, Android remote-UI design, hosting cutover sequence, and test matrix are now documented in `docs/architecture/BATCH_0_TARGET_DESIGN.md`.
- Paper lifecycle and crash-boundary source audit is now documented in `docs/architecture/BATCH_0_PAPER_LIFECYCLE_AUDIT.md`. It confirms atomic four-leg Paper ledger transactions and conservative restart replay protection, and identifies the missing successful-entry receipt replay after a lost HTTP response plus required crash-injection/PostgreSQL concurrency tests.
- Exact-symbol repository search found the generic `DualExecutionEngine`, in-memory idempotency/confirmation/safety helpers, watchdog, and fill-accounting helpers referenced only by their own modules and tests; no production order orchestration caller was found. Dynamic/reflection/external runtime use is not ruled out.
- Still required: complete per-broker capability and session-isolation audit; capture runtime scanner/API/WebSocket baselines; inspect Android navigation/release workflow; and verify VPS deployment observability/state.
- Open a separate implementation PR only after these remaining runtime and integration audits are complete and reviewed.

## Safety decision

**Live trading remains OFF.** Do not enable broker order routing, loosen owner checks, remove the legacy deployment, or alter scanner subscriptions as part of this audit.
