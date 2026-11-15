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
- Acquires PostgreSQL session-level advisory lock `pg_advisory_lock(74192831, 1)` before schema inspection and holds it on the same SQLAlchemy connection through migration completion.
- Runs the main DDL/backfill batch in a transaction on that locked connection; a small initial inspection/legacy guard and the optional Box paper-trade column additions are handled before the main DDL transaction.
- Contains dialect-specific handling for generated IDs in IV tables (`SERIAL` on PostgreSQL, `INTEGER` on SQLite).
- Uses `CREATE INDEX IF NOT EXISTS` for several indexes, but column additions are guarded by a prior inspection followed by `ALTER TABLE`.
- Has no visible migration revision ledger, ordered revision identifiers, or downgrade mechanism in this module.

The PostgreSQL session lock now serializes the initial schema inspection as well as subsequent DDL across independent connections. The SQLite path remains intentionally lock-free and is covered by its own sequential migration tests.

## 3. Concurrent-startup risk (requires a reproducer)

The original inspect-then-alter race was addressed by merged PR #505. `run_schema_migrations()` now acquires `pg_advisory_lock(74192831, 1)` before schema inspection and releases it in `finally`, using the same SQLAlchemy connection throughout. This is a PostgreSQL session-level lock (not transaction-scoped), so it remains held across the DDL transaction boundaries.

The isolated PostgreSQL regression test starts two concurrent caller threads against the same synthetic legacy schema and verifies both complete and the expected schema exists. This exercises concurrent connections within one test process; it is not a multi-process or multi-host deployment rehearsal. No production race was observed.

## 4. CI coverage boundary

The inspected `.github/workflows/ci-tests.yml` starts PostgreSQL 16, but sets the main test process's `DATABASE_URL` to `sqlite:///:memory:`. It separately sets `IV_TEST_POSTGRES_URL` for tests that explicitly opt into that URL.

Therefore:
- A green default Backend Tests run still does not prove that importing `app.main` and executing the entire application startup sequence succeeds against PostgreSQL 16.
- PR #505 adds direct `run_schema_migrations()` tests using an isolated per-test PostgreSQL schema: minimal legacy upgrade, repeatability, generated IDs, Box snapshot expiry backfill, `(user_id, fill_id)` uniqueness, concurrent callers, and injected DDL failure/rollback/retry.
- These fixtures are synthetic/minimal. They do not represent every production legacy schema or prove all app-startup operations (`Base.metadata.create_all`, IV guards, lifespan recovery) end-to-end.

## 5. Required non-production test matrix

Keep the isolated PostgreSQL migration suite and extend it toward a representative, sanitized legacy schema and full application-startup sequence. Do not confuse the direct migration-function tests with an end-to-end `app.main` startup test.

| Test | Setup | Required assertion |
|---|---|---|
| Fresh database startup | Empty isolated PostgreSQL database | Import/startup completes; expected tables, indexes, and guards exist |
| Legacy schema upgrade | Minimal synthetic legacy schema on SQLite and PostgreSQL | Direct migration function completes; selected columns/indexes/backfills verified (passed) |
| Repeated startup | Run migration twice against same synthetic schema | Direct migration function is repeatable; selected schema remains valid (passed) |
| Concurrent startup | Two concurrent caller threads/connections against one synthetic PostgreSQL schema | Both callers complete; expected schema is present (passed). Multi-process/multi-host rehearsal remains open |
| Mid-migration failure | Inject a SQLAlchemy DDL error late in the main migration transaction | Earlier transactional DDL rolls back; advisory lock releases; retry succeeds (passed) |
| Backfill integrity | Seed weekly/monthly/unknown/malformed Box snapshot payloads | Only known classifications are backfilled; unknown/malformed values remain NULL (passed) |
| Constraint/index validation | Insert duplicate `(user_id, fill_id)` values in isolated schema | Duplicate fill identity is rejected; multiple NULL fill IDs remain allowed (passed) |
| Restart after migration | Re-run migration function after injected failure on the same isolated schema | Retry succeeds after rollback/lock release (passed); full API process restart remains open |

Test assertions should include schema introspection, row counts, stable key checks, and exact before/after values for backfilled records. Fixtures must be synthetic or sanitized; never copy production credentials or sensitive trading data into CI.

## 6. Implementation gates before changing migration code

1. Decouple schema migration from API module import and run an explicit migration stage once per deployment; keep readiness fail-closed until the expected schema is present.
2. Retain the PostgreSQL session-level advisory lock until the explicit runner is implemented; document its stable key and lock ownership. Keep SQLite behavior separately tested.
3. Add a migration revision ledger with ordered IDs/checksums and atomic completion records.
4. Extend PostgreSQL coverage to a sanitized representative legacy schema, independent OS processes, full `app.main` startup, and application restart.
5. Use additive expand-and-contract changes; keep legacy Paper ledger/API compatibility and never auto-attribute ambiguous historical rows.
6. Rehearse backup/restore and rollback against a disposable PostgreSQL database before production rollout.
7. Keep Live Trading fail-closed. Do not change Box/IV scanner subscriptions, broker WebSockets, or production deployment as part of migration hardening.

## 7. Decision

**Concurrent PostgreSQL startup and injected DDL failure/rollback/retry are now covered by isolated tests in PR #505, and Backend plus Integration CI passed for head `291356de8a5ad1899fa8972d3740e88121bcd89e`.** Full production-schema rehearsal, backup/restore, explicit migration revision ledger, and decoupling schema changes from module import remain unverified. No production database migration was run as part of this audit.
