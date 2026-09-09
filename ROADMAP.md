# Algo Trading — Master Roadmap

## Vision
Fast, modular, mobile-first advanced F&O algo-trading platform. GitHub is the source of truth. Build small verified checkpoints; no large untested dumps.

## Current Product Focus — Locked
1. **Universal Advanced Backtesting** — one event-driven engine for any strategy, with **Index 1-second data as the primary backtesting focus**. Support daily/1-minute strategies through 1-second, tick, high-resolution and order-book strategies. Use the highest real source resolution required by the strategy; never fabricate higher-frequency events from candles. Historical window is up to 1 year, or the maximum reliable/legally available period when less is available.
2. **Live Scanner** — real market data during market hours, with strategy-specific data requirements and durable results.
3. **Live Paper Trading** — real-market-data simulation for any supported strategy/instrument/order type. Real broker order routing is OFF.
4. **High-resolution data accumulation** — when live 1-second/tick/order-book data is available and required, persist it incrementally for future backtesting; do not collect high-resolution data for strategies that do not need it.

## Stock/Future Data Rule — Locked
- **Stock Cash and Stock Futures both target genuine 1-second data** whenever the source actually provides it.
- If genuine 1-second history is unavailable for a stock/future contract, retain the **highest genuine source resolution available**; never synthesize 1-second/tick/microsecond observations from lower-resolution candles.
- Upcoming/live stock-future 1-second events must be **appended incrementally to the existing historical dataset**, not stored as a separate duplicate dataset.
- Historical + upcoming data must remain durable, deduplicated and restart-safe; late-arriving/gap-repair data merges into the same store.
- Stock-future identity must preserve exact exchange/segment, token, symbol, expiry, lot size and tick size so different expiries cannot be mixed accidentally.
- Stock Cash vs Stock Future, Near vs Next, Calendar Spread, rollover and other comparisons/backtests must be **expiry-aware and expiry-to-expiry by default**, with explicit lifecycle handling at contract boundaries.
- Comparison/backtest results must expose **graph-ready series** for price, basis/spread, trades, P&L, drawdown and overlays, with expiry/date/contract selection.
- The same accumulated source dataset is reusable across scanners, comparisons and backtests; strategy modules must not download duplicate copies of the same market history.

## Backtesting Priority — Locked
- **Priority #1: Index derivatives**, especially Index Futures and Index Options.
- **Primary historical resolution: 1-second real market data** wherever reliably/legal available.
- Index contracts are identified by exact exchange/segment/token/expiry identity; Near/Next/Far lifecycle and rollover are preserved.
- NIFTY, BANKNIFTY and other currently eligible index contracts are discovered dynamically from instrument metadata rather than hardcoded symbols.
- Tick/order-book/event data is supported when genuinely available; **1-second data is never reverse-engineered into fake tick/microsecond data**.
- Calendar Spread, Cash–Future Arbitrage, Synthetic Future, Options multi-leg and event-driven strategies share the same 1-second-capable engine.
- Existing 1-year Cash + Near Future + Next Future datasets are common reusable base data; strategy modules must not create duplicate downloads.
- After the Index 1-second foundation is stable, extend the same engine to stocks, BSE and commodities.

## Advanced Backtesting — Locked Enhancements
- Realistic execution simulator: market/limit/stop orders, bid/ask, spread, slippage, latency, partial fills, rejection, cancellation, queue assumptions and multi-leg execution.
- Market-impact/liquidity model using available quantity and depth when source data exists; theoretical fills are never presented as executable fills.
- Strategy-specific data requirements and subscriptions so only required instruments/resolution are downloaded.
- Multi-resolution replay: 1-second primary for index backtests, plus tick/order-book/event replay when available.
- Contract lifecycle engine: expiry, rollover, lot-size/tick-size changes, contract replacement and exact historical contract identity.
- Maximum-gap/opportunity engine: timestamp, duration, spread, executable spread, MFE/MAE, entry/exit, charges, slippage and missed opportunity analysis.
- Portfolio-level backtesting with capital allocation, dynamic position sizing, margin competition, exposure limits and correlation-aware risk.
- Regime-aware analysis: trending/sideways/high-volatility/low-volatility/crash conditions and strategy performance by regime.
- Walk-forward validation: train → validation → out-of-sample cycles.
- Parameter robustness: sensitivity maps, stable parameter regions and overfitting/fragility detection.
- Monte-Carlo and execution stress testing across trade order, slippage, latency, liquidity, partial fills and missing-data scenarios.
- Survivorship-bias protection using the historical instrument universe valid at each timestamp.
- Look-ahead protection for prices, quotes, depth, expiry metadata, corporate data and all strategy inputs.
- Data-quality score attached to each backtest: coverage, missing events, stale quotes, source provenance and timestamp quality.
- Full audit replay: reproduce exactly what data/events were visible to the strategy before each decision and trade.
- Reproducible backtest identity: dataset/version + engine version + strategy version + parameters + execution assumptions.
- Separate gross, theoretical/executable and net P&L; charges and execution effects remain auditable.

## Non-negotiable product principles
- UI must be advanced, attractive and colourful but simple to operate.
- UI is decoupled from data/strategy/execution so cards, charts, colours, ordering and visibility can be customized later without rewriting core logic.
- Reliability and data accuracy take priority over visual speed.
- Prefer official primary data first; never silently substitute stale/unknown data.
- Performance first: avoid blocking UI work, redundant requests, excessive polling and unbounded memory growth.
- Paper and live trading remain isolated and fail-safe.
- **Real-money algo trading remains disabled until explicitly approved after all safety, idempotency, reconciliation and E2E gates pass.**

## Data Source Priority
1. NSE/BSE official feeds/APIs/licensed data where available.
2. RBI, SEBI, Government/Ministry and other official regulatory/exchange sources.
3. Official company filings/disclosures.
4. Official broker APIs for broker-specific quotes, orders, positions, funds and execution state.
5. High-quality secondary providers only when a primary source is unavailable; preserve source and timestamp/freshness metadata where supported.

## Architecture & Performance
- Python/FastAPI for orchestration/data/backtesting; C++ fast core only when justified by profiling.
- Android native/Kotlin + mobile-friendly web dashboard.
- WebSocket/live streams where supported; avoid unnecessary polling.
- Separate source adapters from normalized market/event models, signal engine, API and UI.
- Broker abstraction/registry supports multiple simultaneous connections.
- Bounded caches, request coalescing, pagination and incremental persistence for large workloads.
- No full-year/full-F&O minute or 1-second ledger held in RAM.
- Event-driven replay must preserve source timestamps/sequence and must not invent higher-frequency observations.
- Target low software processing latency based on measured benchmarks; never promise exchange execution latency.

## Universal Backtesting
- [ ] Extend the existing candle backtest foundation into a strategy-agnostic event-driven engine.
- [x] Add normalized market events for BAR, QUOTE, TRADE, DEPTH and CUSTOM event types.
- [x] Add explicit nanosecond/microsecond/millisecond timestamp normalization without fabricating events.
- [x] Add ordered event replay with optional event-type filtering and tests.
- [x] Connect event replay to generic strategy → signal → risk → execution interfaces.
- [ ] Add realistic execution simulator: market/limit/stop, slippage, latency, partial fills, rejection, cancellation, multi-leg execution and charges.
- [ ] Support strategy-specific data requirements so only required resolution/instruments are downloaded.
- [ ] **Build and verify Index 1-second replay/backtesting path first.**
- [ ] Support candle/tick/high-resolution/order-book backtests through the same engine.
- [ ] Support up to 1 year historical data, or the maximum reliable/legally available period when less is available.
- [ ] Validate Cash–Future, Calendar Spread, Synthetic Future, Options and Order Book strategies against appropriate source-resolution datasets.
- [ ] Add maximum-gap/opportunity detection and executable-vs-theoretical opportunity reports.
- [ ] Add walk-forward, parameter robustness, Monte-Carlo and stress-test modes.
- [ ] Add historical-universe/survivorship-bias protection and complete audit replay.

## Index-First Backtesting Phases
- [ ] Phase 1 — Index instrument master: dynamic exchange/segment/token/expiry/lot/tick metadata.
- [ ] Phase 2 — Index 1-second historical acquisition, coverage audit, gap planning and resumable durable download.
- [ ] Phase 3 — SQLite incremental merge with duplicate/conflict protection and bounded memory.
- [ ] Phase 4 — 1-second event replay + strategy → signal → risk → execution integration.
- [ ] Phase 5 — realistic execution, latency, slippage, partial-fill and multi-leg simulation.
- [ ] Phase 6 — Calendar Spread, Cash–Future Arbitrage, Synthetic Future and Options strategies.
- [ ] Phase 7 — maximum-gap/opportunity engine, audit replay and advanced analytics.
- [ ] Phase 8 — walk-forward, robustness, Monte-Carlo and stress testing.
- [ ] Phase 9 — expand the same verified engine to BSE, stocks and commodities without duplicating the core.

## Live Paper Trading
- [ ] Replace the current fixed-demo paper order stub with a persistent production-grade paper broker.
- [ ] Common strategy/execution contract shared by paper and future live adapters.
- [ ] Real-time LTP/quote/depth driven fills and MTM.
- [ ] BUY/SELL, market/limit/SL, quantity/lots, multi-leg orders, modify/cancel/square-off.
- [ ] Partial fills, rejection states, realistic latency/slippage and charges.
- [ ] Positions, order book, trade book, realized/unrealized P&L, margin/capital tracking.
- [ ] Idempotency, reconciliation, restart recovery and durable audit ledger.
- [ ] Persist required live tick/high-resolution data for future backtesting.

## Live Scanner & Analysis
- [ ] Cash–Future scanner on live market data.
- [ ] Strategy-specific live data subscriptions; high-resolution only where required.
- [ ] Scanner result opens dedicated Live Analysis/P&L screen.
- [ ] Actual paper fills, quantity, average fill, live price and applicable charges drive paper P&L.
- [ ] Combined F&O strategy payoff graph with break-even, max profit/loss, profit/loss zones and live-price marker.
- [ ] Paper/live separation and reconciliation safeguards.

## Data Lifecycle — Core Rule
1. Startup checks/updates missing historical/off-market data.
2. Historical sync is normalized and deduplicated.
3. Live streaming starts only after the required startup gate.
4. Live streams operate during actual market hours; holidays/off-market periods stop unnecessary streaming.
5. Manual historical/off-market sync remains available.
6. Historical data is idempotent: same token + timeframe + timestamp cannot create duplicates.
7. Required high-resolution live events are appended durably so future backtests can reuse them.

## Verified Foundation So Far
- Backend/app foundation and health checks.
- Existing Angel One market-data client/provider boundary.
- Historical provider aligned with existing client contract.
- Historical → live startup gate.
- Stable candle normalization layer.
- Candle storage identity checkpoint verified with unique `(token, timeframe, timestamp)` and upsert semantics.
- Strategy Builder foundation verified.
- Existing candle Backtesting Engine foundation and CAGR/Sharpe/Sortino/expectancy/drawdown metrics verified through CI checkpoints.
- Durable incremental Full-F&O result sinking, cancellation handling, bounded cleanup, cursor-paged API and bounded Android viewer implemented through verified checkpoints.
- Angel One broker connection and real-trading safety/kill-switch foundation exists.
- Cash–Future scanner and basic paper execution path exists.
- Generic high-resolution market-event model and replay foundation added in the current checkpoint.
- Generic event Strategy → Signal → Risk → Execution pipeline foundation added with deterministic tests.

## Multi-Broker
- [ ] Common adapter interface: authenticate/connect/disconnect, live quotes/stream, positions, orders, order status, holdings, funds/margin and supported contract metadata.
- [ ] Broker registry + connection manager with independent state per broker.
- [ ] Normalize broker instrument/token mappings.
- [ ] Angel One remains first tested live adapter.
- [ ] Additional brokers only after current official APIs are verified and integration-tested.
- [ ] Aggregate and broker-wise portfolio/P&L without duplicating orders accidentally.

## Command Center UI
- [ ] Compact Home/Trading Command Center: NIFTY, BANK NIFTY, India VIX, market status, capital, P&L.
- [ ] Market regime, money flow and data-driven “Why Market Is Moving?” card.
- [ ] High-signal India/global macro dashboard.
- [ ] Sector strength, breadth and smart-money panels.
- [ ] Official-first event/order radar.
- [ ] Responsive charts: price trend, P&L/payoff, sector strength, institutional flow, OI/volume and macro trends.
- [ ] Semantic colours: positive/negative/warning/information based on market meaning.
- [ ] Current/Previous/Change/% Change and freshness/source display for major metrics.
- [ ] UI configuration hooks so layout, cards, charts, theme and visibility can be customized later.

## Alerts & Event Radar
- [ ] Custom Alert Builder: WHAT → CONDITION → THRESHOLD → LEVEL → DELIVERY.
- [ ] Company orders, FII/fund activity, broker upgrades, sector rotation, unusual price/volume/OI, macro and commodity alerts.
- [ ] In-app notification, sound/vibration, quiet hours and alert history.

## Profile & Product Polish
- [ ] Editable profile, change password and verified forgot-password flow.
- [ ] WhatsApp share.
- [ ] Free plan now; premium-ready structure without payment requirement.
- [ ] Theme/layout customization.

## Quality Gates
Before a checkpoint is complete:
- Backend tests pass.
- Android build passes.
- Relevant integration tests pass.
- Long-running paths have bounded memory.
- No credentials committed to Git.
- Data provenance/freshness is preserved where supported.
- UI remains responsive under scanner/backtest refresh.
- Real live order routing remains disabled until safety, idempotency and reconciliation gates are explicitly verified.

## Development Rule
**One step → inspect → implement → test → fresh CI → verify PASS → update roadmap/checkpoint.**
