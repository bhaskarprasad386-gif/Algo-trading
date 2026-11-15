# Batch 0 — PostgreSQL Startup Migration Validation Audit

**Status:** Initial source audit superseded in part by merged PR #505. No production database was queried or migrated.
**Audited base:** `main` at `8da655f0c287251cf1602cae484714a7c26fcf15`; hardening commit `196098587d1e26cc24ff6c29f0fc684da0e73a37`.
**Related PR:** #504 — Batch 0 architecture audit.

## 1. Verified startup ordering

The inspected `backend/app/main.py` imports `run_schema_migrations` and invokes these operations at module import time, in this order:

1. `run_schema_migrations()`
2. `Base.metadata.create_all(bind=engine)`
3. `install_iv_basket_immutability_guards(engine)`

This means importing the ASGI application performs database DDL/DML before the application lifespan starts. A deployment with multiple Uvicorn/Gunicorn workers, overlapping old/new processes, or two replicas starting together can therefore cause more than one process to enter migration logic concurrently.

## 2. Verified migration implementation

The inspected `backend/app/core/schema_migrations.py`:

- Uses SQLAlchemy inspection to snapshot table/column existence.
- Returns early when the `users` table is absent.
- Performs a large sequence of conditional `ALTER TABLE`, `CREATE TABLE`, index creation, and data backfills.
- Uses one `engine.begin()` transaction for the main sequence after the initial inspection.
- Contains dialect-specific handling for generated IDs in IV tables (`SERIAL` on PostgreSQL, `INTEGER` on SQLite).
- Uses `CREATE INDEX IF NOT EXISTS` for several indexes, but column additions are guarded by a prior inspection followed by `ALTER TABLE`.
- Has no visible migration revision ledger, ordered revision identifiers, or downgrade mechanism in this module.

The transaction is useful for grouping the migration statements, but it does not itself serialize two application processes that both inspect the same pre-migration schema before either begins its DDL transaction.

## 3. Concurrent-startup risk (requires a reproducer)

The original implementation had a concrete race around inspection-then-alter operations. PR #505 now serializes PostgreSQL migration calls before schema inspection; the original race scenario is covered by a two-thread PostgreSQL regression test.

1. Process A and process B both inspect the schema and observe a column is absent.
2. A adds the column and commits.
3. B attempts to add the same column based on its earlier inspection.
4. Depending on database/DDL timing, B can fail with a duplicate-column error and abort application import/startup.

Similar races can affect create-table/index and data-backfill paths. This is a risk inferred from the inspected control flow, not a claim that a production race has been observed.

The merged implementation uses `pg_advisory_lock(74192831, 1)` and releases it in a `finally` block on the same SQLAlchemy connection. This is a PostgreSQL session-level lock, not a transaction-scoped lock. The test suite exercises two concurrent callers against the same isolated PostgreSQL schema.

## 4. CI coverage boundary

The inspected `.github/workflows/ci-tests.yml` starts PostgreSQL 16, but sets the main test process's `DATABASE_URL` to `sqlite:///:memory:`. It separately sets `IV_TEST_POSTGRES_URL` for tests that explicitly opt into that URL.

Therefore:
- A green default Backend Tests run does not prove that importing `app.main` and executing the full startup migration succeeds against PostgreSQL 16.
- It does not prove concurrent startup safety.
- It does not prove that all PostgreSQL-specific DDL, constraints, indexes, or backfills behave correctly on a representative legacy schema.
- Existing selected PostgreSQL tests may still provide valuable coverage; their presence should not be confused with a full startup migration test.

## 5. Required non-production test matrix

Create a dedicated isolated PostgreSQL test job (or separate test process) that sets `DATABASE_URL` to the CI PostgreSQL service and does not reuse the SQLite-configured test process.

| Test | Setup | Required assertion |
|---|---|---|
| Fresh database startup | Empty isolated PostgreSQL database | Import/startup completes; expected tables, indexes, and guards exist |
| Legacy schema upgrade | Fixture built from a captured/minimal supported pre-migration schema | Startup completes and expected columns/indexes/backfills are correct |
| Repeated startup | Run migration twice against same database | Second run is a no-op in effect; no duplicate DDL or changed historical values |
| Concurrent startup | Launch two independent processes against same empty/legacy DB | Both terminate successfully or one waits safely; no duplicate-DDL failure |
| Mid-migration failure | Inject failure at controlled statement boundary | Transactional DDL/DML rollback behavior is documented and verified for supported PostgreSQL |
| Backfill integrity | Seed classified and unclassified Box/IV history rows | Only source-proven values are backfilled; unknown values remain unknown |
| Constraint/index validation | Seed duplicate and boundary data in isolated fixtures | Expected uniqueness and foreign-key constraints are enforced without destructive cleanup |
| Restart after migration | Stop and start app process against migrated DB | Startup is repeatable; scanners and broker collectors remain disabled in the test |

Test assertions should include schema introspection, row counts, stable key checks, and exact before/after values for backfilled records. Fixtures must be synthetic or sanitized; never copy production credentials or sensitive trading data into CI.

## 6. Implementation gates before changing migration code

1. Introduce an explicit migration runner invoked once under a database-level lock, rather than relying on every application import to race through schema inspection.
2. For PostgreSQL, use a transaction-scoped advisory lock with a stable documented key, or a dedicated migration job/leader lock with equivalent cross-process guarantees. Define a separate safe strategy for SQLite if it remains supported.
3. Add a migration revision ledger with ordered IDs and checksums; record completion atomically with each migration.
4. Keep app startup from serving traffic until the migration runner has completed and readiness confirms the expected revision.
5. Use additive expand-and-contract changes; keep legacy Paper ledger/API compatibility and never auto-attribute ambiguous historical rows.
6. Rehearse backup/restore and rollback against a disposable PostgreSQL database before production rollout.
7. Keep Live Trading fail-closed. Do not change Box/IV scanner subscriptions, broker WebSockets, or production deployment as part of migration hardening.

## 7. Decision

**Concurrent PostgreSQL startup and injected DDL failure/rollback/retry are now covered by isolated tests in PR #505, and Backend plus Integration CI passed for head `291356de8a5ad1899fa8972d3740e88121bcd89e`.** Full production-schema rehearsal, backup/restore, explicit migration revision ledger, and decoupling schema changes from module import remain unverified. No production database migration was run as part of this audit.
