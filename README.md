# Mobile-First Algorithmic Trading & Arbitrage Platform

A modular, mobile-first algorithmic trading and cross-exchange arbitrage system integrated with Angel One SmartAPI and powered by a FastAPI backend.

## Architecture & Directory Structure
* `backend/`: Core FastAPI server, order execution, market data fetching, and strategy engines.
* `web/dashboard/`: Responsive Tailwind CSS web terminal for real-time monitoring and LTP checking.
* `mobile/android/`: Android native client application (Kotlin) for on-the-go tracking and alerts.

## Key Features
* **Live LTP Inspector**: Real-time market data monitoring across exchanges.
* **Arbitrage Evaluator**: Instant calculation of price spreads and cross-exchange opportunities.
* **FastAPI Backend**: High-performance asynchronous routes for automated execution.

## Verification
Backend CI runs dependency installation, Python compilation, and the pytest suite on pushes to `main` and pull requests. Manual workflow dispatch is also enabled.

## VPS deployment safety
The VPS deploy workflow defers service restarts during market hours by default. An explicitly authorized push commit whose message contains `[force-restart]` overrides that guard and restarts the service; use it only when a market-hours interruption is intended.

The synthetic ATM tracker allows only the same bounded 2-second future/spot timestamp skew as the live quote matcher; stale underlying prices remain rejected. The VPS deploy workflow must be explicitly authorized to restart during market hours when deploying runtime fixes.

## Synthetic live opportunity edge filters
Only fresh, positive raw executable edges are eligible for the live opportunity list. The minimum edge is inclusive: NIFTY 5 points, BANKNIFTY 10, FINNIFTY 5, MIDCPNIFTY 3, BANKEX 10, and SENSEX 10. Configured global minimums may raise, but never lower, these per-index floors. Stock synthetic opportunities retain a 5-point floor. These are raw quote-edge filters, not a guarantee of net profit after fees and slippage.

The index floors are inclusive at equality; stock synthetic rows retain the strict greater-than-5-point rule. The live API route repeats these checks as a final guard, so a stale or unexpected low-edge row cannot bypass scanner qualification.

## Private Box Spread paper trading

The isolated Box Spread paper ledger is single-owner only. Configure these backend environment variables on the server before using its UI:

- `BOX_SPREAD_PAPER_PASSWORD`: a strong, private password supplied through the server environment/secret manager. Do not commit it or expose it through a `NEXT_PUBLIC_*` variable.
- `SECRET_KEY`: a strong server-side signing key of at least 32 characters; non-development environments already require this.

The UI exchanges the password for a short-lived bearer token and stores that token in the browser session only. All paper-ledger read/write/recovery routes require the token; missing configuration fails closed. The live Box Spread opportunity feed remains a separate read-only request. This feature does not submit broker orders. Setting these variables and deploying the feature are separate operational steps; do not restart the production service during market hours without explicit approval.
