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

### Historical-to-Live Startup Rule

When the application/server starts:

1. Check the exchange market calendar.
2. Synchronize missing historical market data first.
3. Validate and merge historical data without duplicates.
4. Start the live WebSocket only when the market is open.
5. Feed live ticks into the same processing/storage pipeline used by historical data.

On weekends, exchange holidays, or outside market hours, live WebSocket/live-stream processing must remain OFF. The implementation must use an exchange trading calendar rather than relying only on Saturday/Sunday rules.

Historical and live data must support seamless merging and recovery after downtime.

---

## 10. Performance

The system should minimize unnecessary latency.

### Latency Target

Target **10–15 ms for the application's own system-side processing path** where technically practical. This is a target, not a guarantee of end-to-end broker/network latency.

Architecture:

WebSocket Tick
|
Tick Receiver
|
In-Memory Processing
|
Rust High-Speed Core
|
Backend WebSocket
|
Android / Website

Performance rules:

* Rust for latency-sensitive/hot-path calculations
* Async I/O
* In-memory state and caching where appropriate
* Batch processing where it reduces overhead
* Avoid unnecessary database writes on every tick
* Background workers for heavy/non-latency-critical work
* Heavy processing must not run on the Android UI thread
* Measure actual latency at each pipeline stage instead of assuming it

The system should expose measurable timestamps/metrics for tick receipt, processing completion, and client delivery so bottlenecks can be identified.

Actual broker, Internet, server, and device latency cannot be guaranteed to remain within 10–15 ms.

### Server Portability

The application must be deployable on a free/low-cost server initially and movable to a paid VPS/cloud/high-performance server later without redesigning the application architecture.

Use portable deployment/configuration (for example, containerized services where appropriate), environment-based secrets, and infrastructure-independent application modules.

Moving to a paid server should primarily require infrastructure/configuration changes rather than rewriting the core application.

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
* Spread time-series chart
* Spread maximum/minimum and timestamps
* Spread opportunity duration
* Entry/exit markers
* Bid/ask and liquidity validation
* Complete raw trade-by-trade data

Backtesting must use the same strategy/calculation logic as live and paper-trading paths wherever practical, so behavior does not diverge between modes.

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

## 25. LOCKED ARCHITECTURE DIRECTION — FUTURE-PROOF BACKTESTING & FAST TESTING

This section supersedes any earlier backtesting/testing layout that conflicts with it.

### 25.1 Architectural Goal

The platform must support current and future backtesting types without creating a separate core engine for each strategy family.

The Universal Backtest Engine must remain generic and capability-driven.

Adding a new backtesting type should normally require a new strategy/plugin, data capability, or execution model rather than modifying the core engine repeatedly.

### 25.2 Universal Core

The target core flow is:

Event/Data Source
|
Replay / Event Scheduling
|
Strategy
|
Order
|
Execution Model
|
Fill
|
Portfolio / Accounting
|
Risk / Metrics
|
Result / Journal
|
Result Storage

The core must be deterministic, isolated per run, streaming-capable, and independent of network/database infrastructure wherever practical.

### 25.3 Event-Driven and Time Model

The engine must not assume candle-only or fixed-interval data.

It must be capable of handling:

* Tick
* Trade
* Quote
* Depth / Order Book
* Candle
* Open Interest
* News / Event
* Custom events

Timestamp precision must support high-resolution strategies, including millisecond and microsecond data where the source data provides it.

Clock implementations must be separated:

* Backtest Clock
* Paper Clock
* Live Clock

Backtests must not depend on the machine's wall clock.

### 25.4 Data Architecture

Data access must be abstracted behind DataSource interfaces/capabilities.

Target implementations:

* In-memory data source for fast tests
* Small deterministic fixture data source
* SQLite data source for persistence/integration tests
* Historical/streaming data source for large backtests

Raw, normalized, and derived data must have clear boundaries.

Large historical datasets must be streamable and must not be unnecessarily materialized into RAM.

Raw historical data must be retained rather than overwritten by derived continuous/synthetic data.

### 25.5 Instrument and Multi-Leg Model

The engine must not be restricted to a single symbol/price stream.

It must support:

* Equity
* Index
* Futures
* Options
* Synthetic instruments
* Multi-instrument strategies
* Multi-leg positions/orders
* Spreads and arbitrage structures

Multi-leg operations must be a first-class capability so future arbitrage/options/spread strategies do not require a separate engine.

### 25.6 Execution Model

Execution must be pluggable.

Target capabilities include:

* Simple deterministic fills
* Brokerage/fees
* Slippage
* Partial fills
* Liquidity constraints
* Order-book execution
* Market impact
* Custom execution models

Fast tests must use lightweight deterministic execution. Heavy realism must remain available to heavy backtests.

### 25.7 Portfolio and Accounting

Portfolio/accounting must be independent from broker/network/database infrastructure.

It must handle at minimum:

* Cash
* Positions
* Average price
* Realized P&L
* Unrealized P&L
* Equity
* Margin where applicable

Accounting/statistics must be reusable by backtest and paper-trading paths wherever practical.

### 25.8 Run Context and Isolation

Each backtest/paper run must have an isolated Run Context containing the run-specific:

* Run ID
* Configuration
* Clock
* Data source
* Strategy
* Execution model
* Portfolio
* Result store
* Randomness/seed where applicable

Avoid mutable global state.

Configuration must not be silently mutated during a run.

Deterministic seeded randomness must be available for execution models that require randomness.

### 25.9 Results, Journal, Checkpoint and Resume

The core must produce structured results without requiring persistent storage for every fast test.

Target result stores:

* In-memory result store
* Persistent result store

Large/long-running backtests must support checkpoint/resume where technically appropriate.

Result/journal persistence must be separated from the core calculation path so fast tests do not pay unnecessary I/O cost.

### 25.10 Strategy / Backtesting Extensibility

The Universal Engine must be reusable for:

* Cash-Future
* Futures
* Options
* Arbitrage
* Synthetic instruments
* Order-book strategies
* Market making
* Statistical arbitrage
* OI/event-driven strategies
* Future/custom strategies

A new strategy family must not require cloning the Universal Engine.

### 25.11 Backtest and Paper-Trading Reuse

Backtesting and paper trading should reuse the same strategy/calculation/portfolio contracts wherever practical.

Conceptual flow:

Same Strategy/Core
|
+-- Historical Data + Simulation Execution -> Backtest
|
+-- Live Data + Paper Execution -> Paper Trading
|
+-- Live Data + Controlled Live Execution -> Future Live Trading

Live order execution remains disabled until separately authorized and verified.

### 25.12 Test Architecture

Testing must be split by purpose without reducing total correctness coverage.

#### Fast CI

Use:

* Tiny deterministic datasets
* In-memory data
* In-memory execution
* In-memory results
* Fake/mocked external services
* No unnecessary network
* No large historical datasets
* No unnecessary persistent database I/O

Fast tests must exercise the real core/Universal Engine where practical; they must not become a completely separate fake implementation.

#### Integration CI

Use controlled datasets to verify:

* SQLite/persistence
* Historical adapters
* Data normalization
* Batch processing
* Recovery/restart
* Broker/API boundaries
* Persistent result/journal behavior

#### Heavy Backtesting CI

Use large or realistic datasets for:

* Millisecond/microsecond replay
* Tick/event streams
* Order-book replay
* Cash-Future
* Arbitrage
* Futures
* Options
* Synthetic instruments
* Rollover
* OI/event strategies
* Multi-leg strategies
* Large SQLite workloads
* Performance/stress tests

Heavy tests must not be deleted, silently skipped, or hidden merely to make normal CI faster.

### 25.13 CI Execution Model

Target GitHub Actions layout:

* Fast CI — normal push/PR feedback
* Integration CI — controlled integration verification
* Heavy Backtesting CI — long-running/full backtest verification

Parallelization must be introduced only after test/run isolation is verified. Do not blindly introduce parallel workers where SQLite, files, global state, ordering, or shared resources can cause false failures.

### 25.14 Performance Principle

The objective is not to make computationally heavy backtests artificially short.

The objective is to prevent small correctness tests from paying the cost of:

* large data preparation
* historical materialization
* database I/O
* network access
* heavy execution simulation
* unnecessary persistence

Runtime must be measured before and after architectural changes.

### 25.15 Refactoring Rules

Do not rewrite the platform wholesale.

For each architectural change:

1. Inspect current source and tests.
2. Identify the exact boundary/coupling problem.
3. Preserve correct existing behavior.
4. Make the smallest safe refactor.
5. Run relevant tests.
6. Run broader verification.
7. Commit only verified work.
8. Update project checkpoint/changelog.

Classification remains:

* Verified bug/problem -> fix
* Verified robustness/functional problem -> fix
* Uncertain -> verify before changing
* Correct -> leave untouched

### 25.16 Locked Implementation Order

The architecture refactor and test-speed work must follow this order:

1. Baseline/current-system freeze
2. Current architecture and dependency audit
3. Core contracts/boundaries
4. Event and time model
5. Data architecture
6. Execution architecture
7. Portfolio/accounting
8. Universal Backtest Engine
9. Strategy/backtest adapters
10. Result/journal/checkpoint system
11. Fast test suite
12. Integration test suite
13. Heavy backtest suite
14. CI workflow separation
15. Safe performance optimization/parallelization
16. Full verification and runtime comparison

No implementation step should be skipped merely because a later step appears easier.

### 25.17 Future-Backtesting Compatibility Rule

The architecture must be evaluated against future unknown strategies, not only today's Cash-Future/Arbitrage tests.

Before adding a major new backtesting family, verify whether it can be expressed using existing:

* Event types
* DataSource capabilities
* Instrument model
* Multi-leg model
* Execution model
* Portfolio/accounting contracts
* Strategy interface

Only add a new core capability when source/test evidence shows an existing abstraction cannot correctly represent the requirement.

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
