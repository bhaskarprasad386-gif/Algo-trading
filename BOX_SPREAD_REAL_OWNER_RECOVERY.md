# Box Spread Real-Owner Binding Recovery Runbook

## Status and safety boundary

This is an operator procedure for the dedicated Box Spread real-owner binding in
`box_spread_real_owner_state`. It is not an automated recovery feature.
Until an audited recovery tool and session reconciliation exist, account-switch
rejection must remain fail-closed. Real-trading arming is unavailable and must
not be enabled as part of recovery.

This procedure applies only to Box Spread real-owner binding. Do not change
Synthetic Future, Cash-Future, Calendar Spread, paper-trading state, or shared
broker/feed state.

## Required approvals and evidence

1. Open an incident/change record and obtain approval from two authorized
   maintainers: one to verify ownership and one to execute/review the recovery.
2. Verify the intended broker account through the broker's authenticated flow
   outside this recovery operation. Never request or record passwords, TOTP
   seeds, API secrets, or raw client codes in the incident.
3. Record the incident ID, approvers, UTC timestamp, reason, repository commit,
   and old/new keyed fingerprints only. Never log raw account identity or
   signing keys.
4. If the account identity cannot be independently verified, stop. Do not clear
   the row or permit a different account to claim ownership.

## Recovery steps

1. Keep real-trading arming disabled. Confirm the owner endpoint still returns
   HTTP 503 for enable requests and the kill switch is engaged.
2. Disconnect the Box Spread broker adapter and confirm no Box Spread session
   is registered. Do not restart production services or place broker orders as
   part of this procedure.
3. Take and verify a protected database backup. Restrict access to the backup
   and never copy credentials or raw account identifiers into it outside the
   existing database protections.
4. Inspect the single `owner_key = 'box-spread-real-owner'` row. Confirm its
   current fingerprint and flags. If duplicate owner rows or unexpected state
   exist, stop and escalate; do not guess which row is authoritative.
5. Execute a reviewed, version-controlled, transactional maintenance operation
   that changes only the Box Spread owner binding to the independently verified
   account fingerprint. The operation must preserve the row identity, write an
   external audit event with incident ID and old/new fingerprints, and set
   `connected = false`, `real_trading_enabled = false`, and
   `kill_switch = true`. Do not perform an ad-hoc production SQL edit.
6. Verify the transaction committed, exactly one owner row remains, the new
   fingerprint matches the verified account, and all fail-closed flags are
   correct. Verify the old fingerprint is no longer bound.
7. Reconnect only through the authenticated owner flow. Confirm the adapter is
   disconnected if the durable claim fails. Confirm the persisted flags remain
   trading OFF and kill switch ON.
8. Record verification evidence in the incident and obtain the second
   maintainer's sign-off. Any unexpected result means stop and restore from the
   verified backup through the approved change process.

## Explicit non-goals

- This runbook does not implement an in-app/manual recovery endpoint.
- It does not authorize live trading, production deployment, or service restart.
- It does not replace session lifecycle persistence, broker order reconciliation,
  durable kill-switch operations, or independent execution-risk review.

Until those capabilities are implemented and tested, a successful account
recovery does not make the system merge-ready for live execution.
