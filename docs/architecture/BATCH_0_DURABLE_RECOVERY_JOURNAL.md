# Batch 0 — Durable Emergency Recovery Journal

## Scope

This change adds a SQLAlchemy persistence adapter for the existing broker-neutral
`RecoveryJournal` contract. It is not wired to a broker adapter and does not
enable live routing.

## Durable controls

- Account + strategy kill switches are latched persistently. This adapter has
  no implicit clear operation.
- Account + intent recovery state, frozen flag, and risk reservation are
  persisted separately from the entry-coordinator journal.
- Instrument leases are unique by account + symbol. Acquisition attempts the
  entire basket in one transaction; a conflicting instrument rejects the
  entire acquisition.
- Per-account, intent, and symbol recovery budgets require explicit approval,
  have a fixed maximum attempt count and cumulative notional cap, and cannot
  be silently replaced or enlarged.
- Each close authorization requires a previously claimed attempt and exact
  `quantity × limit_price = notional` equality.
- Recovery events are append-only and sequenced within the intent.
- Risk reservation and leases can be released only after the persisted state
  is broker-confirmed `FLAT`; `FLAT` is terminal.

## Provisioning contract

The owning execution/risk layer must explicitly call
`acquire_instrument_leases` and `configure_recovery_budget` before invoking
recovery. Missing leases, missing budget, denied budget, exhausted attempts,
or exceeded notional all fail closed. This PR intentionally does not invent
or auto-approve limits.

## Safety boundaries / remaining certification

- No broker adapter, order route, credential, live setting, VPS deployment,
  service restart, scanner, WebSocket, or subscription is changed.
- A kill switch is durable state; entry-routing code must consult it before
  authorizing any new entry. This adapter does not silently wire that policy
  into broker routes.
- PostgreSQL multi-worker race certification, process-crash recovery tests,
  production migration rollout, and broker-specific recovery certification
  remain required before any live use.
- The entry journal and recovery journal remain separate durable records; a
  later integration step must link them through a single intent lifecycle.
