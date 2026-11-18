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

## Coordinator-level ownership decision (fail-closed)

Row-level locks around budget claims and close approvals do **not** serialize a
complete `EmergencyRecoveryCoordinator.recover()` run. The coordinator performs
broker I/O between journal transactions, so two workers for the same
(account_id, intent_id) can each progress through different transactions and
submit distinct close client IDs against the same residual exposure.

The recovery owner must therefore be a durable, intent-scoped capability, not
an expiring heartbeat:

1. The first worker atomically claims a unique owner generation for the
   account/intent before it performs any recovery broker I/O. A competing
   worker must fail closed without changing recovery state or contacting the
   broker.
2. Ownership is never transferred merely because a timestamp expired, a
   heartbeat stopped, or a database connection disappeared. A worker can be
   paused while a broker request is still in flight; automatic expiry could
   permit a second close request.
3. Loss of ownership or inability to prove ownership immediately before an
   external broker action aborts the run into MANUAL_REVIEW. The coordinator
   must not proceed on a cached ownership result.
4. A process restart does not silently inherit the old owner. It remains
   blocked until an explicit operator-controlled takeover has been recorded.
   Takeover requires an authoritative broker order/position reconciliation,
   proof that prior close authorizations are reconciled, and a durable
   operator identity/reason. If any evidence is missing or ambiguous, retain
   the existing owner and risk reservation and require manual review.
5. Owner generation is monotonic and included in every ownership event and
   broker-action authorization record. The generation is checked in the same
   durable transaction that authorizes each action. Broker adapters must
   support idempotent client order IDs; fencing in the database cannot retract
   a request already accepted by a broker.

### Required implementation and test gates

- Two independent coordinator instances racing the same intent: exactly one
  obtains ownership; the loser makes zero broker calls.
- A second coordinator cannot enter while the first is paused between quote,
  budget approval, and broker submission.
- Owner generation changes only through the explicit operator takeover path.
- Simulated process restart with an unresolved close authorization: takeover
  denied and no broker call made.
- Reconciled terminal close plus authoritative positions and explicit
  operator approval: takeover can be recorded; old generation is rejected.
- Database errors or ownership-check failures immediately before cancel,
  quote-dependent close submission, and finalization all fail closed.
- Worker pause longer than any configured operational timeout never enables
  automatic takeover.
- Concurrent takeover attempts: at most one new generation is committed.

Until these gates pass against PostgreSQL with independent sessions, the
coordinator remains uncertified for multi-worker or live broker execution.
No TTL lease, automatic stale-owner reclamation, or assumption that a
PostgreSQL advisory lock alone fences an in-flight broker request is accepted.
