# Independent VPS diagnostics

## Design

The existing `/api/v1/diagnostics/errors` route is process-local and its in-memory
buffer is lost on backend restart. The dashboard cannot use it while the API is down.

`algo-diagnostics.service` is an independent systemd process. It reads only the
`algo-trading.service` journal, stores sanitized ERROR/WARNING/CRITICAL summaries
in bounded SQLite storage, and serves read-only JSON on `127.0.0.1:8765`.
It does not import the trading app, open broker sockets, call runner controls, or
mutate scanner state.

## Install on VPS

After deploying the repository to `/opt/Algo-Trading`:

```bash
sudo install -m 0644 /opt/Algo-Trading/deploy/algo-diagnostics.service /etc/systemd/system/algo-diagnostics.service
sudo systemctl daemon-reload
sudo systemctl enable --now algo-diagnostics.service
sudo systemctl status algo-diagnostics.service --no-pager
curl --fail http://127.0.0.1:8765/health
curl --fail 'http://127.0.0.1:8765/api/errors?limit=20'
```

## HTTPS routing required for mobile browser

The browser must reach the collector while API port 8000 is down. Add
`deploy/nginx-server-diagnostics.conf` inside the existing HTTPS `server {}`
block (do not replace TLS or existing routes), run `nginx -t`, then reload Nginx.
If another TLS reverse proxy is used, configure the equivalent same-origin route.

Do not expose port 8765 publicly. It binds to loopback only. The endpoint returns
sanitized summaries, not raw journal metadata or environment values. Treat the
reverse-proxy path as operationally sensitive and verify the actual public HTTPS
route before relying on it.

## API contract and bounds

- `GET /health`: independent collector health.
- `GET /api/errors?limit=1..200`: newest sanitized journal events.
- 14-day retention, maximum 3,000 events by default.
- Exposes event id, server timestamp, severity, component, sanitized message,
  and source only.

The mobile page keeps the last successful response in component state and
browser-local cache. A failed refresh marks the collector unreachable but never
replaces previous logs with an empty list. It shows the last successful sync time.

## Deployment and verification

The VPS deploy workflow must install/verify the independent service and the
existing HTTPS reverse proxy must be updated separately. Do not claim browser
access during API outage until the public HTTPS route is verified on the VPS.

Verify in staging: agent survives API stop/restart; journal warnings/errors appear;
sample credentials are redacted; invalid limits are bounded; event retention is
bounded; the public route works over HTTPS with API port 8000 stopped. Do not
touch market-data sockets, scanners, Shared Runner, broker connections, or order
mode as part of this diagnostics work.
