# Independent VPS diagnostics

## Design

The existing `/api/v1/diagnostics/errors` route is process-local and its in-memory
buffer is lost on backend restart. The dashboard cannot use it while the API is down.

`algo-diagnostics.service` is an independent systemd process. It reads the
`algo-trading.service` journal and, while the API is reachable, snapshots the
process-local `/api/v1/diagnostics/errors` ring buffer into bounded SQLite storage.
It serves persisted ERROR/WARNING/CRITICAL events on `127.0.0.1:8765`. Error
messages and operational details are intentionally visible on the public dashboard.
Credential and account-secret patterns are redacted before persistence and response.
It preserves runtime diagnostics captured before an API outage and journal errors
emitted while the API is down. It does not import the trading app, open broker
sockets, call runner controls, or mutate scanner state.

## Install without restarting the trading API

For an already deployed checkout at `/opt/Algo-Trading`, run:

```bash
sudo bash /opt/Algo-Trading/deploy/install-diagnostics-only.sh
```

This installer updates/enables/restarts only `algo-diagnostics.service` and
checks its loopback health endpoint. It does not fetch or merge Git changes,
install backend dependencies, or restart/reload `algo-trading.service`. It does
not restart broker sockets or market-data runners.

The general `deploy/bootstrap-vps.sh` is a full backend deployment script. It
fast-forwards `main`, installs backend dependencies and restarts
`algo-trading.service`; do not use it for a diagnostics-only rollout when
preserving live API/socket continuity is required.

Verify locally on the VPS:

```bash
curl --fail http://127.0.0.1:8765/health
curl --fail 'http://127.0.0.1:8765/api/errors?limit=20'
systemctl is-active algo-diagnostics.service
systemctl is-active algo-trading.service
```

## HTTPS routing

The collector binds only to loopback. Do not expose port 8765 publicly.
The `/server-diagnostics/` HTTPS reverse-proxy route is intentionally public,
matching the existing dashboard's no-login access model. Error logs and
operational diagnostics are therefore viewable by anyone who can reach the
dashboard. Credential and account-secret patterns are redacted by the collector;
this is not an authentication boundary and should not be described as private.

Add the location from `deploy/nginx-server-diagnostics.conf` inside the existing
HTTPS `server {}` block without replacing TLS or existing routes. Run
`nginx -t` and reload Nginx only after the configuration test succeeds. Verify
the public HTTPS route and verify that the collector remains loopback-only.

## API contract and bounds

- `GET /health`: independent collector and SQLite readiness, plus API process reachability.
- `GET /api/errors?limit=1..200`: newest sanitized journal/runtime events.
- 14-day retention, maximum 3,000 events by default.
- Exposes event id, server timestamp, severity, component, sanitized message,
  and source only.

The dashboard and mobile error page use the independent collector, not the API
error route. The mobile page keeps the last successful response in component state
and browser-local cache. Dashboard Shared Runner polling uses the dedicated
`/api/v1/market-data/shared-index-runner/health-lite` endpoint, which reports
per-underlying/per-instrument freshness and socket lifecycle without serializing
the full decorated 52-contract quote snapshot. The collector reports API process
reachability separately from collector health. Failed refreshes do not replace
previous logs with an empty list; the last successful sync time remains visible.

## Acceptance checks

- Collector service remains healthy independently of `algo-trading.service`.
- Journal warnings/errors appear and sample credentials are redacted.
- API runtime events captured before an outage remain available.
- Event retention and request limits remain bounded.
- Public HTTPS diagnostics route works while API port 8000 is stopped.
- Collector port 8765 remains inaccessible directly from the public network.

Do not touch market-data sockets, scanners, Shared Runner, broker connections, or
order mode as part of this diagnostics rollout.
