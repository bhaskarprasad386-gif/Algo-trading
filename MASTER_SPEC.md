# ALGO TRADING PLATFORM — MASTER SPEC

## Project Status

Current Phase: FOUNDATION
Current Step: 1A — Master Specification
Scanner Development: NOT STARTED
Live Order Execution: DISABLED

---

## 1. Main Goal

Build a mobile-first, fast, stable and fully customizable algorithmic trading platform.

The platform must work through:

* Android application
* Web dashboard
* Independent backend

Android and Web must use the same backend and account.

---

## 2. Technology Stack

### High-Speed Core

Rust

### Backend/API

Python + FastAPI

### Android

Kotlin + Jetpack Compose

### Website

TypeScript + React

### Database

SQLite initially

### Real-Time Communication

WebSocket

### Notifications

Firebase

### Charts

Lightweight charting system suitable for strategy payoff and market charts

### Backtesting

Python-based engine

---

## 3. Mobile-First Rule

The final user must NOT need Termux to operate the application.

Termux is not a final-system dependency.

The user should be able to:

1. Open Android App
2. Login
3. See dashboard
4. Receive live data
5. Receive alerts
6. Use scanners
7. Use charts
8. Use backtesting
9. Use paper trading
10. Control algo trading

Website must provide the same core account functionality.

---

## 4. Architecture

Angel One
|
+-- REST API
|
+-- WebSocket
|
v
High-Speed Market Core
|
v
Backend API
|
+-----+-----+
|           |
Android      Website
|           |
+-----+-----+
|
Same Account
Same Data

---

## 5. Development Order

The project must be developed in this order:

1. Foundation
2. Backend Core
3. Database
4. Authentication
5. Angel One Integration
6. WebSocket Market Data
7. High-Speed Data Processing
8. Android Application
9. Website
10. Android/Web Synchronization
11. Charts
12. Alerts
13. Backtesting
14. Paper Trading
15. Risk Engine
16. Order Engine
17. Strategy Engine
18. Scanner Framework
19. Individual Scanners
20. Live Algo Trading
21. Optimization
22. Final Testing

---

## 6. Scanner Rule

Scanner development must NOT start before the base platform is stable.

Every scanner must be an independent module.

Adding or modifying one scanner should not unnecessarily modify other scanners or the core.

Future examples:

* RSI Scanner
* Future vs Spot
* Put-Call Parity
* Cash Arbitrage
* Breakout
* Volume Spike
* Wyckoff
* Divergence
* Custom scanners

---

## 7. Strategy Builder

The architecture must support a visual strategy builder similar in concept to professional options strategy platforms.

Strategy components may include:

* Buy/Sell
* Call
* Put
* Strike
* Premium
* Quantity
* Expiry
* Futures
* Spot
* Entry price
* Exit price

The strategy engine must be separate from the UI.

---

## 8. Strategy Payoff Graph

The system must support a strategy payoff/profit-loss graph.

The graph should be generated from the same calculation engine used by:

* Strategy Builder
* Backtesting
* Paper Trading
* Algo Trading

Possible outputs:

* Profit/Loss by underlying price
* Breakeven
* Maximum profit
* Maximum loss
* Entry point
* Exit point
* Risk/reward information

The graph must be extensible for future strategies.

---

## 9. Market Data

Angel One SmartAPI will be used after the foundation is ready.

Expected data:

* LTP
* Bid
* Ask
* Volume
* OHLC
* Equity
* Index
* Futures
* Options
* Instrument token
* Timestamp

The system must support:

* WebSocket reconnect
* Heartbeat
* Subscription management
* Connection monitoring
* Duplicate protection
* Error recovery

---

## 10. Performance

The system should minimize unnecessary latency.

Architecture:

WebSocket Tick
|
Tick Receiver
|
Memory Processing
|
High-Speed Core
|
Backend WebSocket
|
Android / Website

Heavy processing must not run on the Android UI thread.

Actual broker/network latency cannot be guaranteed to be 1 millisecond.

The goal is minimum practical system-side latency.

---

## 11. Android Performance

Android must remain responsive even when many instruments are being monitored.

Rules:

* Heavy processing on backend
* Efficient WebSocket updates
* Virtualized lists
* Controlled chart data
* Memory management
* Reconnect handling
* Graceful error handling
* Crash recovery

The application must not freeze because of market-data processing.

---

## 12. Database

Initial database: SQLite.

Expected entities:

* Users
* Sessions
* Instruments
* Subscriptions
* Market data
* Candles
* Watchlists
* Alerts
* Scanner configurations
* Scanner results
* Strategies
* Backtests
* Orders
* Positions
* Trades
* P&L
* System logs

Data must support:

* Duplicate prevention
* Indexing
* Recovery
* Historical/live merge

---

## 13. Authentication

Authentication must be shared by Android and Website.

Security rules:

* No API secrets in source code
* Environment-based secrets
* Session management
* Access control
* Secure authentication

---

## 14. Alerts

Future architecture:

Signal
|
Alert Engine
|
Firebase
|
Android Notification

Alerts must also appear in the application and website feed.

---

## 15. Backtesting

Backtesting must support:

* Historical data
* Entry
* Exit
* Stop loss
* Target
* Brokerage
* Slippage
* P&L
* Win rate
* Drawdown
* Trade list
* Equity curve

Backtesting must not place real orders.

---

## 16. Paper Trading

Paper trading must be completed and tested before live orders.

Flow:

Signal
|
Risk Engine
|
Paper Order
|
Virtual Position
|
Virtual P&L

---

## 17. Risk Engine

The system must support:

* Maximum orders per day
* Maximum quantity
* Maximum position
* Maximum loss
* Duplicate-order protection
* Strategy enable/disable
* Market-hour restrictions
* Kill switch
* Emergency stop
* Paper/Live separation

---

## 18. Live Order Engine

Live orders must remain disabled during development.

Final architecture:

Strategy
|
Signal
|
Risk Engine
|
Order Manager
|
Angel One API
|
Order Status
|
Position Manager
|
P&L

All orders must be logged.

---

## 19. Broker Compliance

Before live trading:

* SmartAPI requirements must be verified
* Static IP requirements must be verified
* Algo-ID requirements must be verified
* API limits must be verified
* Order restrictions must be verified
* Current broker/regulatory requirements must be verified

No assumption should be treated as confirmed broker policy.

---

## 20. Testing Rule

Every module follows:

Build
|
Run
|
Test
|
Stress Test
|
Fix
|
Verify
|
Git Commit
|
Next Step

A broken module must not be ignored while building the next dependent module.

---

## 21. Save & Resume Rule

After every completed step:

1. Code is saved
2. Tests are performed
3. Git commit is created
4. CHANGELOG is updated
5. Project status is updated
6. Next step is recorded

Completed work must not be unnecessarily rewritten.

The project must always be resumable from the last verified checkpoint.

---

## 22. No Giant Code Rule

Do not create the entire platform as one huge file.

Use independent modules.

Small working modules must be tested before adding the next module.

---

## 23. Current Status

Foundation: IN PROGRESS

Master Specification: CREATED

Backend: NOT STARTED

Database: NOT STARTED

Angel One: NOT STARTED

WebSocket: NOT STARTED

Android: NOT STARTED

Website: NOT STARTED

Charts: NOT STARTED

Alerts: NOT STARTED

Backtesting: NOT STARTED

Paper Trading: NOT STARTED

Risk Engine: NOT STARTED

Order Engine: NOT STARTED

Strategy Builder: NOT STARTED

Scanner Framework: NOT STARTED

Scanners: NOT STARTED

Live Algo: DISABLED

---

## 24. Current Next Step

STEP 1B — Project Folder Structure

Do not start scanner development until the foundation is completed and verified.
