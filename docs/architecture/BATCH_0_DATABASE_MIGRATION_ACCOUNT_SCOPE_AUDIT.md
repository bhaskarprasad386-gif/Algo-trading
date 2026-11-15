# Batch 0 — Database Migration and Account-Scope Readiness Audit

**Status:** Source audit only; no schema was changed and no production database was queried.  
**Audited base:** `main` at `8da655f0c287251cf1602cae484714a7c26fcf15`.  
**Decision:** The current schema bootstrap is not yet a safe foundation for the multi-user, multi-account Paper/Live execution model. Preserve it for compatibility while introducing a versioned, additive migration path.

## 1. Current schema lifecycle

The inspected `backend/app/main.py` executes these operations at module import:

1. `run_schema_migrations()`
2. `Base.metadata.create_all(bind=engine)`
3. `install_iv_basket_immutability_guards(engine)`

The custom migration function is in `backend/app/core/schema_migrations.py`. It is an imperative collection of table/column existence checks and raw SQL DDL/DML. The inspected implementation has no migration revision ledger, explicit target revision, downgrade path, or per-migration deployment record.

**Operational consequence:** importing the API can mutate the database. Starting multiple workers or multiple application versions during a rolling deployment can cause concurrent schema inspection/DDL races. A database migration should instead be an explicit, serialized deployment step, completed before application workers start.

## 2. Current account identity constraints

### TradingAccount

The inspected `backend/app/models/account.py` defines:
- `TradingAccount.user_id` as a non-null foreign key to `users.id`;
- `unique=True` on `user_id`;
- one `mode` value, defaulting to `PAPER`;
- Paper balance/P&L fields.

This enforces at most one `TradingAccount` row per user. It cannot represent multiple broker accounts for the same user, or a Paper account plus several broker-linked Live accounts.

There is also a second `TradingAccount` declaration in `backend/app/models.py`, with the same table name and a different field definition. It also declares `user_id` unique, but does not declare the foreign key shown in `models/account.py`. The project imports model modules through `backend/app/models/__init__.py`; before changing account models, trace all import paths and metadata registration so a migration does not accidentally target a different declaration than the active application metadata.

### Orders and positions

The inspected generic `Order` and `Position` models use nullable `user_id` columns and do not have a `trading_account_id` or broker-account foreign key. The Order table has a unique `(user_id, fill_id)` index for fill idempotency; this is not an account-scoped identity. The Position table has a partial unique index for one open Paper position per user/symbol, not per account.

**Required target:** every new execution intent, order, fill, position, risk reservation, and broker event must be scoped to a stable `trading_account_id` and authorized user. Do not repurpose `user_id` or Paper `account_key` as the broker account ID.

## 3. Existing migration behavior and risk

The current startup migration performs a broad set of changes, including:
- adding user and alert-contact columns/tables;
- altering trading-account balance fields and inferring initial balances from current virtual balance, realized P&L, and open Paper positions;
- adding generic order and position columns;
- creating backtest job/result tables and indexes;
- adding Box Spread snapshot expiry classification and backfilling only values already present in persisted JSON;
- creating durable IV option/spot observation and signal-event tables and uniqueness indexes;
- adding generic order fill-idempotency and audit-chain indexes;
- extending IV notification outbox lease fields.

Positive safeguards observed:
- many operations check table/column existence before adding;
- Box snapshot backfill explicitly avoids guessing expiry type from a later-loaded instrument master;
- legacy IV OI timestamps remain NULL when their original observation time cannot be proven;
- several source-identity unique indexes make sampler/retry writes idempotent.

Risks requiring verification before multi-account migrations:
1. **No version ledger:** there is no durable record of which logical migration has been applied to a database.
2. **Import-time execution:** application startup and schema mutation are coupled.
3. **Mixed dialect behavior:** the migration has dialect-specific handling for selected generated IDs, but the full DDL/DML path needs a real PostgreSQL migration test; SQLite success is not proof of PostgreSQL compatibility.
4. **Data-dependent indexes:** unique indexes can fail on legacy duplicates. A preflight duplicate report and explicit remediation policy are required before applying new account-scoped unique constraints.
5. **Backfill semantics:** inferred balances and legacy rows need a recorded provenance; do not silently attach ambiguous historical orders/positions to a new broker account.
6. **Locking/rolling deployment:** no migration advisory lock or deployment-level single-run guarantee was identified in this file.
7. **Rollback:** raw DDL and data backfills have no paired downgrade. Rollback must be expand/contract and application-version compatible, not a destructive reverse script.

## 4. CI database coverage: what it proves and what it does not

The inspected `.github/workflows/ci-tests.yml` provisions PostgreSQL 16 and exports `IV_TEST_POSTGRES_URL`, but the main backend pytest invocation sets `DATABASE_URL=sqlite:///:memory:`. The workflow excludes several integration/historical test files from that main command.

Therefore:
- a green main backend test job primarily proves the SQLite-configured test suite;
- PostgreSQL coverage is limited to tests that explicitly consume `IV_TEST_POSTGRES_URL`;
- the workflow does not, by itself, prove the complete startup migration succeeds against an existing PostgreSQL production-like schema;
- no production backup restore or concurrent-worker migration rehearsal is evidenced by this workflow.

Before accepting the account migration, add a dedicated PostgreSQL job that starts from both an empty schema and a representative legacy schema, applies the migration exactly once, retries it, and verifies all constraints/data invariants.

## 5. Safe migration plan for multi-account execution

Use an expand-and-contract sequence. Do not remove old columns or constraints in the first rollout.

### Phase A — inventory and backup
- Take and verify a restorable PostgreSQL backup.
- Inventory table row counts, nullability, indexes, foreign keys, duplicate candidates, and current account ownership.
- Export a redacted legacy ownership report for orders, positions, Paper ledger accounts, broker owner state, and fills.
- Mark ambiguous/unowned legacy records as legacy/read-only; never guess a broker account.

### Phase B — additive account model
- Add stable `trading_accounts.id` identity with user FK, account type/mode, broker, broker-side account reference/fingerprint, display label, lifecycle state, and timestamps.
- Replace the one-account-per-user uniqueness only after new account identity and compatibility reads are deployed. Enforce uniqueness on the actual business identity, e.g. user + broker + broker account reference, not user alone.
- Add nullable `trading_account_id` foreign keys to new execution/order/fill/position/risk/event tables first.
- Add account-scoped idempotency constraints, for example (account_id, client_request_id) and (account_id, broker, broker_order_id), after duplicate preflight.
- Keep raw credentials out of these tables; store only a vault/secret-manager reference and non-secret fingerprint.

### Phase C — dual-read, controlled backfill
- Continue serving existing Paper endpoints and ledger without changing their behavior.
- Backfill only records with deterministic ownership evidence.
- Store migration provenance (source, timestamp, mapping rule, operator/change ID).
- Leave ambiguous rows unassigned and read-only.
- Compare legacy and new account balances, positions, fills, and realized P&L before switching any read path.

### Phase D — execution cutover
- Route new Paper intents through the durable account-scoped lifecycle only after parity tests pass.
- Keep broker adapters disconnected and Live disabled.
- Validate concurrent duplicate requests, partial fills, process death at each transition, reconciliation, and per-account isolation on PostgreSQL.
- Only after a separately reviewed release should old constraints be retired.

## 6. Mandatory PostgreSQL validation matrix

| Scenario | Required result |
|---|---|
| Empty PostgreSQL database | Schema creation/migrations complete deterministically |
| Legacy SQLite-derived fixture imported into PostgreSQL | Existing records preserved; no guessed ownership |
| Migration run twice | Second run is a no-op with same schema/data |
| Two migration processes race | One acquires migration lock; the other waits/fails safely |
| Duplicate legacy fill identity | Preflight reports conflict; migration does not silently discard or merge |
| Two accounts for one user, same broker | Both identities coexist and remain isolated |
| Same broker account reference attached to two users | Rejected unless explicit shared-account ownership model is approved |
| Orders/fills/positions without account ID | Remain legacy/unassigned and cannot enter Live routing |
| Rollback to previous application version | Old code continues to read expanded schema |
| Restore rehearsal | Restored database passes integrity and ownership checks |

## 7. Non-regression constraints

- No changes to Box Spread or IV scanner schema/data in this audit.
- No new market-data subscriptions, WebSocket clients, or scanner callbacks.
- No production migration, restart, deploy, or database query was performed.
- Paper execution remains on its current route until an approved compatibility migration is implemented.
- Live remains fail-closed and OFF.

**Batch 0 exit condition:** A versioned migration plan, a verified backup/restore, legacy ownership classification, and a PostgreSQL migration/concurrency report must exist before implementation of multi-account execution begins.