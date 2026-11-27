# Algo Trading Platform

A mobile-first algorithmic trading platform with a FastAPI backend, web dashboard, and native Android client.

## Architecture

- `backend/`: API services, broker integrations, execution safety, and market-data infrastructure.
- `frontend/`: Responsive web dashboard and strategy workspaces.
- `mobile/android/`: Native Android client.

## Live market-data ownership

- `SharedIndexMarketDataRunner` is the sole production owner of the Angel One common WebSocket.
- The live index universe is restricted to NIFTY and BANKNIFTY.
- IV Skew Flip reads the spot and option records already published by Shared Runner. It does not create or own a broker WebSocket or additional broker subscriptions.
- No retired strategy may register a consumer with the production common WebSocket manager.

## Retired strategy scope

Cash-Future, Synthetic-Future, Calendar Spread, and Box Spread have been retired from live runtime ownership and public application surfaces. Their strategy APIs, runners, feeds, subscriptions, page-push channels, web/mobile workspaces, paper-trading components, and legacy operational workflows are removed.

Historical database records and data-only compatibility models are retained. Retirement does not drop or truncate historical tables.

## VPS deployment safety

The VPS deploy workflow defers service restarts during market hours by default. An explicitly authorized push commit whose message contains `[force-restart]` overrides that guard and restarts the service; use it only when a market-hours interruption is intended.

## Verification

Backend, frontend, integration, Android, shared-feed, and off-market runtime checks run through GitHub Actions. Retired strategy artifacts must not be reintroduced into startup, routes, subscriptions, or scheduled workflows.
