# Batch 0 — PostgreSQL recovery concurrency certification tests

This batch adds integration tests against the CI PostgreSQL service using
isolated per-test schemas and independent SQLAlchemy sessions.

Covered races:

- Twelve concurrent workers claim an explicitly approved recovery budget with
  a three-attempt ceiling. Exactly three claims may succeed and the durable
  counter must remain exactly three.
- Eight concurrent intents compete for the same two-instrument basket lease.
  Exactly one intent may own the complete basket.
- A contender that collides on one instrument while attempting to reserve a
  second instrument must leave no partial lease behind.

The tests skip locally when `IV_TEST_POSTGRES_URL` is absent; CI configures
PostgreSQL for backend tests. These tests validate database locking and unique
constraint behavior, not broker execution or exchange atomicity.

Live routing remains disabled. This does not certify broker adapters, process
crash recovery, deployment migrations, or production PostgreSQL topology.
