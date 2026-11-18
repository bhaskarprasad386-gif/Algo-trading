# ALGO TRADING PLATFORM — MASTER SPEC

## Project Status

Current Phase: ARCHITECTURE / BACKTESTING REFACTOR
Current Step: Architecture audit and controlled refactor
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

The application must remain portable across low-cost/free and later paid infrastructure.

---

## 5. Development Order

The original platform roadmap remains, but the current priority is the architecture/backtesting refactor before further feature expansion.

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

Live execution remains disabled.

---

## 6. Scanner Rule

Scanner development must NOT start before the base platform is stable.

Every scanner must be an independent module.

Adding or modifying one scanner should not unnecessarily modify other scanners or the core.

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

Historical and live data must support seamless merging and recovery after downtime.

The exchange calendar, not only weekday rules, must control market-open/live-stream behavior.

---

## 10. Performance

The system should minimize unnecessary latency.

Target 10–15 ms for the application's own system-side processing path where technically practical. This is a target, not an end-to-end broker/network guarantee.

Performance rules:

* Rust for latency-sensitive/hot-path calculations where justified
* Async I/O
* In-memory state and caching where appropriate
* Batch processing where it reduces overhead
* Avoid unnecessary database writes on every tick
* Background workers for heavy/non-latency-critical work
* Heavy processing must not run on the Android UI thread
* Measure actual latency at each pipeline stage

The architecture must be portable between low-cost and higher-performance servers without requiring a core rewrite.

---

## 11. Android Performance

Android must remain responsive even when many instruments are monitored.

Heavy processing belongs on the backend.

---

## 12. Database

Initial database: SQLite.

Expected entities include:

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

Data must support duplicate prevention, indexing, recovery, and historical/live merge.

---

## 13. Authentication

Authentication must be shared by Android and Website.

No API secrets in source code.

---

## 14. Alerts

Signal -> Alert Engine -> Firebase -> Android Notification.

Alerts must also appear in the application and website feed.

---

# 15. LOCKED ARCHITECTURE — FUTURE-PROOF BACKTESTING + FAST TESTING

This section is the current source of truth for backtesting architecture and supersedes earlier conflicting layouts.

## 15.1 Architectural Goal

The platform must support current and future backtesting types without creating a separate core engine for each strategy family.

Adding a new backtesting type should normally require a new strategy/plugin, data capability, instrument capability, or execution model—not cloning or repeatedly modifying the Universal Backtest Engine.

## 15.2 Universal Core

Target flow:

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

The core must be:

* generic
* deterministic
* isolated per run
* streaming-capable
* infrastructure-independent wherever practical
* reusable by backtest and paper trading wherever practical

The core must not require network, broker API, or persistent database I/O for ordinary calculations.

## 15.3 Event + Time Model

The engine must be event-driven and must not assume candle-only or fixed-interval data.

Supported event families must be extensible to:

* Tick
* Trade
* Quote
* Depth / Order Book
* Candle
* Open Interest
* News / Event
* Custom events

Timestamp precision must preserve source precision and support millisecond/microsecond strategies where the source provides that precision.

Separate clocks:

* Backtest Clock
* Paper Clock
* Live Clock

Backtests must not depend on machine wall-clock time.

## 15.4 Data Architecture

Use a DataSource abstraction/capability layer.

Target implementations:

* In-memory source — fast tests
* Small deterministic fixture source — component tests
* SQLite source — persistence/integration
* Historical/streaming source — large backtests

Data flow:

Raw Data
-> Normalized Data
-> Derived Data
-> DataSource
-> Event Stream
-> Engine

Raw historical data must not be overwritten by continuous/synthetic/derived data.

Large historical datasets must be streamable and must not be unnecessarily materialized into RAM.

Caching may accelerate repeated access but must remain outside the core engine contract.

## 15.5 Instrument + Multi-Leg Model

The engine must support:

* Equity
* Index
* Futures
* Options
* Synthetic instruments
* Multi-instrument strategies
* Multi-leg positions/orders
* Spreads
* Arbitrage structures

Multi-leg operations are a first-class capability.

Future arbitrage/options/spread strategies must not require separate engines.

## 15.6 Execution Model

Execution must be pluggable.

Target capabilities:

* Simple deterministic fills
* Brokerage/fees
* Slippage
* Partial fills
* Liquidity constraints
* Order-book execution
* Market impact
* Custom execution models

Fast tests use lightweight deterministic execution.

Heavy realistic backtests may use richer execution models.

Paper execution must be a separate adapter while sharing common order/fill contracts.

## 15.7 Portfolio + Accounting

Portfolio/accounting must be independent from broker/network/database infrastructure.

Minimum support:

* Cash
* Positions
* Average price
* Realized P&L
* Unrealized P&L
* Equity
* Margin where applicable

The same accounting contracts should be reusable by backtest and paper trading wherever practical.

## 15.8 Run Context + Isolation

Every backtest/paper run gets an isolated RunContext containing:

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

Randomized execution models must support deterministic seeds for reproducible tests.

Run isolation must be strong enough to support safe future parallel execution.

## 15.9 Results + Journal + Checkpoint

The core must return structured results without requiring persistent storage for fast tests.

Target stores:

* In-memory result store
* Persistent result store

Large/long-running backtests should support checkpoint/resume where technically appropriate.

Result/journal persistence must be outside the hot calculation path so fast tests do not pay unnecessary I/O cost.

## 15.10 Strategy/Backtest Extensibility

The same Universal Engine must be capable of running:

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

A new strategy family must not require cloning the engine.

Before adding a new family, verify whether existing event, data, instrument, multi-leg, execution, portfolio, and strategy contracts can represent it. Add a new core capability only when source/test evidence shows the existing abstractions cannot correctly represent the requirement.

## 15.11 Backtest + Paper Trading Reuse

Conceptual flow:

Same Strategy/Core
|
+-- Historical Data + Simulation Execution -> Backtest
|
+-- Live Data + Paper Execution -> Paper Trading
|
+-- Live Data + Controlled Live Execution -> Future Live Trading

Live order execution remains disabled until separately authorized and verified.

## 15.12 Test Architecture

Testing is split by purpose without reducing total correctness coverage.

### Fast CI

Use:

* Tiny deterministic datasets
* In-memory data
* In-memory execution
* In-memory results
* Fake/mocked external services
* No unnecessary network
* No large historical datasets
* No unnecessary persistent database I/O

Fast tests must exercise the real core/Universal Engine wherever practical.

Fast tests must not become a completely separate fake implementation.

### Integration CI

Use controlled datasets to verify:

* SQLite/persistence
* Historical adapters
* Data normalization
* Batch processing
* Recovery/restart
* Broker/API boundaries
* Persistent result/journal behavior

### Heavy Backtesting CI

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

## 15.13 CI Execution Model

Target GitHub Actions layout:

Fast CI
-> normal push/PR feedback

Integration CI
-> controlled integration verification

Heavy Backtesting CI
-> long-running/full backtest verification

The fast workflow must remain a meaningful correctness gate, not merely a compile check.

Parallelization is allowed only after run/test isolation is verified. Do not blindly parallelize tests sharing SQLite, files, global state, ordering, ports, or other mutable resources.

## 15.14 Performance Principle

The goal is not to make computationally heavy backtests artificially short.

The goal is to prevent small correctness tests from paying the cost of:

* large data preparation
* historical materialization
* database I/O
* network access
* heavy execution simulation
* unnecessary persistence

Runtime must be measured before and after changes.

## 15.15 Refactoring Rules

Do not rewrite the platform wholesale.

For every architectural change:

1. Inspect current source and relevant tests.
2. Identify the exact boundary/coupling problem.
3. Preserve correct existing behavior.
4. Make the smallest safe refactor.
5. Run relevant tests.
6. Run broader verification.
7. Commit verified work.
8. Update checkpoint/changelog.

Classification:

* Verified bug/problem -> FIX
* Verified robustness/functional problem -> FIX
* Uncertain -> VERIFY first
* Correct -> untouched

A pytest failure is never dismissed merely because production code appears reasonable. Investigate whether the root cause is production code, test, fixture/data, configuration/environment, dependency/import, or API/contract mismatch, then resolve the verified cause.

## 15.16 Locked Implementation Order

The architecture/backtesting/test-speed work must follow this exact order:

1. Baseline/current-system freeze
2. Current architecture + dependency audit
3. Core contracts/boundaries
4. Event + time model
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
16. Full verification + runtime comparison

No later implementation step should be used to bypass an unresolved earlier boundary problem.

---

# 16. Paper Trading

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

Paper trading should reuse the same strategy, order, fill, portfolio, accounting, and timing contracts as backtesting wherever practical.

---

# 17. Risk Engine

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

# 18. Live Order Engine

Live orders remain disabled during development.

Future flow:

Strategy
|
Signal
|
Risk Engine
|
Order Manager
|
Broker API
|
Order Status
|
Position Manager
|
P&L

All orders must be logged.

---

# 19. Broker Compliance

Before live trading:

* SmartAPI requirements must be verified
* Static IP requirements must be verified
* Algo-ID requirements must be verified
* API limits must be verified
* Order restrictions must be verified
* Current broker/regulatory requirements must be verified

No assumption is treated as confirmed broker policy.

---

# 20. Testing Rule

Every module follows:

Build
|
Run
|
Test
|
Stress Test where appropriate
|
Fix
|
Verify
|
Git Commit
|
Checkpoint
|
Next Step

A broken module must not be ignored while building a dependent module.

---

# 21. Save + Resume Rule

After every completed step:

1. Code is saved
2. Relevant tests are performed
3. Git commit is created
4. CHANGELOG is updated
5. Project status/checkpoint is updated
6. Next step is recorded

Completed work must not be unnecessarily rewritten.

The project must always be resumable from the last verified checkpoint.

---

# 22. No Giant Code Rule

Do not create the entire platform as one huge file.

Use independent modules.

Small working modules must be tested before adding the next module.

---

# 23. Current Status

Foundation: IN PROGRESS

Architecture Direction: LOCKED

Backtesting Architecture: LOCKED FOR CONTROLLED REFACTOR

Fast/Integration/Heavy Test Separation: PLANNED

Scanner Framework: NOT STARTED

Live Algo: DISABLED

Current CI timeout: 90 minutes while baseline/full-suite behavior is being measured

---

# 24. Current Next Step

STEP 1 — BASELINE / CURRENT ARCHITECTURE AUDIT

Do not start the refactor by guessing.

First inspect current main, source dependencies, test dependencies, runtime-heavy paths, database/network usage, global state, and existing backtesting boundaries.

Then produce the exact KEEP / MOVE / REFACTOR / ADD map.

---

## 25. Permanent Source-of-Truth Rule

MASTER_SPEC.md is the canonical architecture/specification reference for this project.

When starting work in a new chat/tab:

1. Read MASTER_SPEC.md from GitHub main.
2. Read the current checkpoint/status.
3. Verify the latest relevant commits.
4. Inspect current source/tests before making assumptions.
5. Continue from the recorded next step.
6. Do not replace locked architecture with a newly invented design without explicit project-level revision.

GitHub main is the source of truth for implemented code and this specification is the source of truth for the locked architecture direction.

