# Box Paper Authenticated WebSocket — Implementation Plan

**Status:** Design/audit baseline; implementation must follow in isolated commits and pass CI before merge.
**Base:** `7c8201b25198f032b51a245909f839ac0778ed95`
**Scope:** Box Spread Paper Trading private read-model updates only. No broker execution changes.

## Existing integration facts

- Paper REST routes already use `require_paper_access` and a dedicated `box-spread-paper` token scope.
- The paper page currently keeps the bearer token in sessionStorage and sends it in REST Authorization headers.
- The page currently refreshes private account/ledger/control data every 7 seconds.
- The app shell's page-push provider currently assigns page channels to selected scanner pages; Paper Trading is not assigned a private channel.
- Browser WebSocket APIs do not support arbitrary Authorization headers. Do not put the long-lived bearer token in a WebSocket URL or loggable query parameter.
- Public Box Spread opportunities are a separate data domain and must remain on the existing public Box Spread push channel.

## Authentication transport decision

Use a short-lived, single-use WebSocket ticket:

1. Browser calls an authenticated REST ticket endpoint with the existing Bearer token.
2. Server validates the exact existing paper owner subject and scope, then issues a cryptographically random opaque ticket with a very short TTL (target 30 seconds).
3. Ticket is bound to the paper owner scope, intended channel, and server-side session/token identity; it is stored server-side or in a shared atomic TTL store.
4. Browser opens the WebSocket with only the opaque one-time ticket (never the long-lived bearer token).
5. Server atomically consumes the ticket before accepting the connection. Expired, replayed, wrong-scope, or wrong-channel tickets are rejected.
6. Do not log ticket values. Require the configured frontend Origin allowlist and enforce message-size, connection, and rate limits.

A cookie-based design is acceptable only if the deployment topology supports secure cross-site cookies end-to-end; do not silently weaken SameSite/Secure/CORS settings to make it work.

## Channel and event contract

Private channel name: `box-spread-paper-private`.

- Initial snapshot after authenticated connection: account balance, portfolio summary, execution-control state, open/closed position page metadata, and durable recovery summary.
- Incremental event envelope: `schema_version`, monotonically increasing per-owner `sequence`, stable `event_id`, `event_type`, `occurred_at`, and bounded `payload`.
- Events: `ACCOUNT_SNAPSHOT`, `TRADE_CREATED`, `ENTRY_COMMITTED`, `EXIT_COMMITTED`, `BALANCE_CHANGED`, `EXECUTION_CONTROL_CHANGED`, `RECOVERY_STATE_CHANGED`.
- Do not broadcast raw SQL rows, credentials, password/token claims, internal exception strings, or another account's data.
- Every publish operation must be emitted only after the corresponding database transaction commits. Prefer a transactional outbox for durable delivery; if the first increment is process-local, explicitly treat it as best-effort and rely on sequence-gap snapshot recovery.

## Client behavior and polling retirement

- On login, obtain ticket and connect; apply snapshot before rendering private data as fresh.
- Apply incremental events only when sequence is exactly the expected next sequence. Ignore duplicate event IDs; on a gap, reconnect and fetch a fresh authorized snapshot.
- On close, network loss, ticket expiry, or auth failure, mark private state stale and disable unsafe state-changing controls until reauthenticated/recovered.
- Remove the 7-second private ledger interval only after authenticated push, reconnect recovery, and event coverage tests pass.
- Keep low-frequency REST fallback only while disconnected and bounded with backoff; retain manual refresh.
- Keep public live opportunities on the existing Box Spread public push channel; do not route them through this private channel.

## Required tests / release gates

1. No ticket without valid bearer token; reject expired/wrong subject/wrong scope.
2. Ticket TTL, one-time consumption, replay rejection, wrong-channel rejection, concurrent consume race.
3. WebSocket Origin allowlist, unauthenticated handshake, connection limits, payload bounds.
4. Owner/account isolation and proof that no private event reaches public subscribers.
5. Initial snapshot correctness and event sequence ordering.
6. Publish only after DB commit; rollback must publish nothing.
7. Reconnect snapshot, sequence gap, duplicate event, stale token, and server restart behavior.
8. Frontend: no private-data polling while connected; bounded fallback only while disconnected; execution controls disabled while private state is stale.
9. Full backend tests, integration tests, frontend verify, and Fast Broker Safety Gate.

## Explicit non-goals / invariants

- No changes to Box Spread runner, scanner calculations, market-data subscriptions, broker adapters, real order routing, or paper fill/ledger transaction semantics.
- No exchange-level atomicity claims; Paper ledger's four-leg DB transaction remains a simulation-only invariant.
- WebSocket is a read-model delivery mechanism, never an execution command channel.
- A disconnect, reconnect, expired token, or failed snapshot must never turn Paper execution ON.
- Do not merge or deploy until all required checks pass and the diff is reviewed.
