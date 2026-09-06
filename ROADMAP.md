# Algo Trading — Master Roadmap

## Vision
Fast, modular, mobile-first advanced F&O algo-trading platform. GitHub is the source of truth. Build small verified checkpoints; no large untested dumps.

## Current Product Focus — Locked
1. **Universal Advanced Backtesting** — one event-driven engine for any strategy, from daily/1-minute strategies through tick, high-resolution and order-book strategies. Use the highest real source resolution required by the strategy; never fabricate millisecond events from candles. Historical window is up to 1 year, or the maximum reliable/legally available period when less is available.
2. **Live Scanner** — real market data during market hours, with strategy-specific data requirements and durable results.
3. **Live Paper Trading** — real-market-data simulation for any supported strategy/instrument/order type. Real broker order routing is OFF.
4. **High-resolution data accumulation** — when live high-resolution/tick/order-book data is available and required, persist it incrementally for future backtesting; do not collect millisecond data for strategies that do not need it.

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
- No full-year/full-F&O minute ledger held in RAM.
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
- [ ] Support candle/tick/high-resolution/order-book backtests through the same engine.
- [ ] Support up to 1 year historical data, or the maximum reliable/legally available period when less is available.
- [ ] Validate Cash–Future, Synthetic Future and Order Book strategies against appropriate source-resolution datasets.

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
