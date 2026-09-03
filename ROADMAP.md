# Algo Trading — Master Roadmap

## Vision
Fast, modular, mobile-first advanced F&O algo-trading platform. GitHub is the source of truth. Build small verified checkpoints; no large untested dumps.

## Non-negotiable product principles
- UI must be advanced, attractive and colourful but simple to operate.
- UI is decoupled from data/strategy/execution so cards, charts, colours, ordering and visibility can be customized later without rewriting core logic.
- Reliability and data accuracy take priority over visual speed.
- Prefer official primary data first; never silently substitute stale/unknown data.
- Performance first: avoid blocking UI work, redundant requests, excessive polling and unbounded memory growth.
- Paper and live trading remain isolated and fail-safe.

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
- Target low software processing latency based on measured benchmarks; never promise exchange execution latency.

## Data Lifecycle — Core Rule
1. Startup checks/updates missing historical/off-market data.
2. Historical sync is normalized and deduplicated.
3. Live streaming starts only after the required startup gate.
4. Live streams operate during actual market hours; holidays/off-market periods stop unnecessary streaming.
5. Manual historical/off-market sync remains available.
6. Historical data is idempotent: same token + timeframe + timestamp cannot create duplicates.

## Verified Foundation So Far
- Backend/app foundation and health checks.
- Existing Angel One market-data client/provider boundary.
- Historical provider aligned with existing client contract.
- Historical → live startup gate.
- Stable candle normalization layer.
- Candle storage identity checkpoint verified with unique `(token, timeframe, timestamp)` and upsert semantics.
- Strategy Builder foundation verified.
- Backtesting Engine foundation and CAGR/Sharpe/Sortino/expectancy/drawdown metrics verified through CI checkpoints.
- Durable incremental Full-F&O result sinking, cancellation handling, bounded cleanup, cursor-paged API and bounded Android viewer implemented through verified checkpoints.
- Angel One broker connection and real-trading safety/kill-switch foundation exists.
- Cash–Future scanner and paper execution path exists.

## Current Next Work — Performance + Reliable Data Foundation
- [ ] Audit live market-data paths for blocking calls, redundant requests and unnecessary polling.
- [ ] Add/standardize source provenance and freshness metadata.
- [ ] Add bounded caching/request coalescing where safe.
- [ ] Add performance regression tests for latency, memory and duplicate requests.
- [ ] Keep long-running Full-F&O results incrementally persisted and paged.

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

## Scanner & Live Analysis
- [ ] Scanner result opens dedicated Live Analysis/P&L screen.
- [ ] Actual live fills, quantity, average fill, live price and applicable charges drive live P&L.
- [ ] Combined F&O strategy payoff graph with break-even, max profit/loss, profit/loss zones and live-price marker.
- [ ] Paper/live separation and reconciliation safeguards.

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
- Live order routing remains disabled until safety, idempotency and reconciliation gates are explicitly verified.

## Development Rule
**One step → inspect → implement → test → fresh CI → verify PASS → update roadmap/checkpoint.**
