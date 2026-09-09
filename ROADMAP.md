# Algo Trading — Master Roadmap

## Vision
Fast, modular, mobile-first advanced F&O algo-trading platform. GitHub is the source of truth. Build small verified checkpoints; no large untested dumps.

The final product must allow a user to build, backtest, compare, scan and paper-trade **any supported strategy** without creating a separate data universe for every strategy. Historical and upcoming market data form one durable, reusable source-of-truth dataset; each backtest is an independent reproducible run over the required slice of that data.

## Current Product Focus — Locked
1. **Universal Advanced Backtesting** — one event-driven engine for any strategy, with **Index 1-second data as the primary backtesting focus**. Support daily/1-minute strategies through 1-second, tick, high-resolution and order-book strategies. Use the highest real source resolution required by the strategy; never fabricate higher-frequency events from candles. Historical window is up to 1 year, or the maximum reliable/legally available period when less is available.
2. **Live Scanner** — real market data during market hours, with strategy-specific data requirements and durable results.
3. **Live Paper Trading** — real-market-data simulation for any supported strategy/instrument/order type. Real broker order routing is OFF.
4. **High-resolution data accumulation** — when live 1-second/tick/order-book data is available and required, persist it incrementally for future backtesting; do not collect high-resolution data for strategies that do not need it.
5. **Advanced arbitrage backtesting** — Box Spread, Synthetic Future/Cash–Future, Calendar Spread, rollover and related multi-leg arbitrage across liquid stock and index F&O, using actual contract/expiry/strike metadata and executable bid/ask where available.

## Backtest Resolution Policy — Locked
For **every backtest independently**, select the finest genuine resolution that is actually available and sufficiently covered for that backtest's exact data scope:

**ms → s → m → h**

- If genuine millisecond data is available for the requested scope, use ms.
- Otherwise use genuine second data.
- Otherwise use genuine minute data.
- Otherwise use genuine hourly data.
- Existing engine support for microsecond/nanosecond timestamps remains available for sources that genuinely provide them, but no finer observations may be fabricated from coarser data.
- A finer-resolution source with incomplete coverage must not be silently mixed with a coarser source in a way that changes event timing. Coverage-aware selection/fallback must be explicit in the run metadata.
- Resolution is chosen **per backtest**, not globally. Two backtests over different instruments, expiries or dates may legitimately use different resolutions.
- Every result must show the actual resolution selected, source/provider, coverage, missing-data count and timestamp quality.
- Never reverse-engineer tick/microsecond/millisecond data from 1-minute/1-second candles.

## Independent Backtest Run — Locked
Every backtest is its own reproducible object and must not inherit hidden state from another run.

Each run must retain:
- unique `backtest_run_id`;
- strategy definition/version and all parameters;
- exact historical start/end date-time;
- instrument universe and exact contract/expiry/token identity;
- selected data resolution and source provenance;
- source data watermark/version used by the run;
- execution model, bid/ask, slippage, latency, liquidity and fee assumptions;
- entry/exit rules and risk configuration;
- complete trade ledger and event/opportunity ledger;
- gross, executable/theoretical and net P&L separately;
- payoff points/curve and break-even levels where applicable;
- graph-ready time series for price, basis/spread, trades, P&L, drawdown and strategy overlays;
- data-quality/coverage report;
- reproducible audit information sufficient to explain exactly why a signal/trade/opportunity occurred.

One backtest must never overwrite another backtest's results. Re-running the same specification must create a deterministic/reproducible result identity or an explicitly versioned new run.

## Historical Strategy Builder — Locked
- A strategy must be buildable from **backdated/historical data**, not only from live data.
- Strategy definitions must be versioned and immutable once a backtest is executed.
- Strategy Builder must support indicator rules, price/quote rules, event/news-style rules, futures, options, multi-leg strategies, arbitrage rules and custom event strategies through the common event-driven interface.
- A historical strategy run must use only information that would have been available at that timestamp; look-ahead is prohibited.
- Strategy parameters used in a run must be stored with the result so the same historical experiment can be reproduced.
- Future extension: visual/no-code strategy construction must compile to the same versioned strategy specification used by the programmatic API.

## Payoff Engine — Locked
- Every options/multi-leg backtest that has a defined payoff must automatically generate payoff data.
- Show break-even point(s), maximum profit, maximum loss, profit/loss zones, expiry payoff and current/historical underlying marker where applicable.
- Payoff must reflect the exact legs, strikes, quantities/lots, premiums and direction used by the backtest.
- For futures/arbitrage strategies, generate the relevant spread/basis/payoff/risk curve instead of forcing an options-only payoff representation.
- Payoff calculations are independent of the execution ledger but must be linked to the same immutable run specification.

## Section Query Box / Historical Explainability — Locked
Every major backtesting, scanner, strategy and analysis section must expose a query capability so the user can ask questions such as:

- “Ye kab hua tha?”
- “Maximum gap kab tha?”
- “Kaunse contract me hua?”
- “Entry/exit price kya tha?”
- “Kaunsi expiry aur kaunsa strike tha?”
- “Us waqt kaun sa data resolution use hua?”
- “P&L kitna bana aur charges/slippage kya tha?”

The answer must be traceable to the stored event/trade/opportunity record and return exact date-time, contract/expiry/token, legs/strikes, prices, direction, resolution and relevant provenance. Natural-language parsing may evolve, but the backend query contract must remain deterministic and auditable.

## Stock/Future Data Rule — Locked
- **Stock Cash and Stock Futures both target genuine 1-second data** whenever the source actually provides it.
- If genuine 1-second history is unavailable for a stock/future contract, retain the **highest genuine source resolution available**; never synthesize 1-second/tick/microsecond observations from lower-resolution candles.
- Upcoming/live stock-future 1-second events must be **appended incrementally to the existing historical dataset**, not stored as a separate duplicate dataset.
- Historical + upcoming data must remain durable, deduplicated and restart-safe; late-arriving/gap-repair data merges into the same store.
- Stock-future identity must preserve exact exchange/segment, token, symbol, expiry, lot size and tick size so different expiries cannot be mixed accidentally.
- Stock Cash vs Stock Future, Near vs Next, Calendar Spread, rollover and other comparisons/backtests must be **expiry-aware and expiry-to-expiry by default**, with explicit lifecycle handling at contract boundaries.
- Comparison/backtest results must expose **graph-ready series** for price, basis/spread, trades, P&L, drawdown and overlays, with expiry/date/contract selection.
- The same accumulated source dataset is reusable across scanners, comparisons and backtests; strategy modules must not download duplicate copies of the same market history.

## Index-First Backtesting Priority — Locked
- **Priority #1: Index derivatives**, especially Index Futures and Index Options.
- **Primary historical resolution: 1-second real market data** wherever reliably/legal available.
- Index contracts are identified by exact exchange/segment/token/expiry/lot/tick identity; Near/Next/Far lifecycle and rollover are preserved.
- NIFTY, BANKNIFTY and other eligible index contracts are discovered dynamically from instrument metadata rather than hardcoded symbols.
- Weekly expiries are included where the exchange/instrument master genuinely provides them; monthly expiries are included for all eligible index contracts.
- Tick/order-book/event data is supported when genuinely available; **1-second data is never reverse-engineered into fake tick/microsecond data**.
- Calendar Spread, Cash–Future Arbitrage, Synthetic Future, Box Spread, Options multi-leg and event-driven strategies share the same 1-second-capable engine.
- Existing 1-year Cash + Near Future + Next Future datasets are common reusable base data; strategy modules must not create duplicate downloads.
- After the Index 1-second foundation is stable, extend the same verified engine to stocks, BSE and commodities.

## Advanced Arbitrage Scope — Locked
### Box Spread
- Backtest long and short Box Spread using actual call/put bid/ask quotes.
- Include all eligible **NIFTY 50 stock F&O** names with sufficient liquidity.
- Stock Box Spread strike-position scope: **3rd, 4th and 5th strike positions from ATM** as requested; do not interpret this as a fixed rupee gap.
- Index Box Spread strike-position scope: **3rd through 15th strike positions from ATM**.
- Strike positions are counted from the actual sorted option chain around the ATM strike; the engine must use real listed strikes and never fabricate missing strikes.
- Support monthly index expiries and weekly index expiries where genuinely available.
- Preserve both sides of the ATM chain where valid and report the exact lower/upper strike positions used.
- Include executable edge after configured fees and execution assumptions, not just theoretical parity.

### Synthetic Future / Synthetic Cash–Carry
- Support both synthetic directions around ATM using actual call/put bid/ask quotes.
- **Stock F&O:** scan up to 5 actual strike positions from ATM on both lower and upper sides.
- **Index F&O:** scan up to 15 actual strike positions from ATM on both lower and upper sides.
- Only liquid instruments/contracts satisfying configured option/future liquidity thresholds are eligible.
- Compare synthetic forward/future economics with the corresponding real future using actual executable sides where available.
- Store the exact option legs, future contract, expiry, strike position, direction, edge, charges and timestamp for every opportunity.

### Cash–Future / Calendar / Rollover
- Cash–Future basis and cash-and-carry backtesting across supported liquid F&O.
- Calendar Spread and expiry-to-expiry basis analysis with exact contract lifecycle.
- Rollover analysis using open interest/volume where reliable data exists.
- No cross-expiry contamination; each contract lifecycle remains independently identifiable.

### Advanced Multi-leg Arbitrage
- Common multi-leg representation for Box, Synthetic, Calendar, Cash–Future, option parity and future extension strategies.
- Atomic logical opportunity record plus leg-level execution records.
- Future support for partial multi-leg fills, leg slippage, latency and hedge failure simulation.

## Advanced Backtesting — Locked Enhancements
- Realistic execution simulator: market/limit/stop orders, bid/ask, spread, slippage, latency, partial fills, rejection, cancellation, queue assumptions and multi-leg execution.
- Market-impact/liquidity model using available quantity and depth when source data exists; theoretical fills are never presented as executable fills.
- Strategy-specific data requirements and subscriptions so only required instruments/resolution are downloaded.
- Multi-resolution replay: per-run ms/s/m/h selection, 1-second primary for index backtests, plus tick/order-book/event replay when genuinely available.
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
- Graph generation is part of the result contract, not a later UI-only feature.

## Data Acquisition & Coverage Pipeline
- [x] Durable SQLite historical catalog with WAL and deterministic identity/hash protection.
- [x] Explicit append API for upcoming/live data.
- [x] Exact duplicate suppression and conflicting-identity protection.
- [x] Late-arriving/gap-repair merge while preserving the latest watermark.
- [x] Session-aware gap planning and chunked historical sync primitives.
- [x] Resumable bounded download executor with retries and durable chunk progress.
- [x] Read-only coverage audit/reporting and completeness checks.
- [x] Stock-future lifecycle primitives preserving expiry-specific identity.
- [ ] Production provider adapters for every required source/resolution.
- [ ] Index 1-second historical acquisition and verified real coverage for all eligible contracts/expiries.
- [ ] Genuine ms data acquisition where a licensed/official source provides it.
- [ ] Automatic startup/update process that finds missing historical ranges and appends only missing data.
- [ ] Upcoming/live data continuously appended into the same reusable historical catalog.
- [ ] Coverage matrix by exchange → instrument → expiry → date range → resolution.
- [ ] Automated data-quality scoring and freshness/provenance dashboard.

## Universal Backtesting
- [ ] Extend the existing candle backtest foundation into a strategy-agnostic event-driven engine.
- [x] Add normalized market events for BAR, QUOTE, TRADE, DEPTH and CUSTOM event types.
- [x] Add explicit nanosecond/microsecond/millisecond timestamp normalization without fabricating events.
- [x] Add ordered event replay with optional event-type filtering and tests.
- [x] Connect event replay to generic strategy → signal → risk → execution interfaces.
- [ ] Add per-run genuine-resolution selection: ms → s → m → h with coverage-aware fallback.
- [ ] Add realistic execution simulator: market/limit/stop, slippage, latency, partial fills, rejection, cancellation, multi-leg execution and charges.
- [ ] Support strategy-specific data requirements so only required resolution/instruments are downloaded.
- [ ] **Build and verify Index 1-second replay/backtesting path first.**
- [ ] Support candle/tick/high-resolution/order-book backtests through the same engine.
- [ ] Support up to 1 year historical data, or the maximum reliable/legally available period when less is available.
- [ ] Validate Cash–Future, Calendar Spread, Synthetic Future, Box Spread, Options and Order Book strategies against appropriate source-resolution datasets.
- [ ] Add maximum-gap/opportunity detection and executable-vs-theoretical opportunity reports.
- [ ] Add automatic payoff/graph generation for every applicable backtest.
- [ ] Add independent run storage, provenance and replay/audit identity.
- [ ] Add section-level historical query/explainability API.
- [ ] Add walk-forward, robustness, Monte-Carlo and stress-test modes.
- [ ] Add historical-universe/survivorship-bias protection and complete audit replay.

## Index-First Backtesting Phases
- [x] Phase 1 foundation — dynamic index instrument normalization and exact contract metadata model added.
- [ ] Phase 2 — Index 1-second historical acquisition, coverage audit, gap planning and resumable durable download.
- [x] Phase 3 foundation — SQLite incremental merge with duplicate/conflict protection and bounded memory.
- [x] Phase 4 foundation — high-resolution event replay + strategy → signal → risk → execution interfaces.
- [ ] Phase 4 completion — verified 1-second index replay/backtesting path against real historical data.
- [ ] Phase 5 — realistic execution, latency, slippage, partial-fill and multi-leg simulation.
- [x] Phase 6 foundation — executable Box Spread and Synthetic Cash–Carry backtester primitives added.
- [x] Phase 6 foundation — configurable ATM-relative arbitrage scan policy added for stock/index strike scopes.
- [ ] Phase 6 completion — dynamic index/stock universe, expiry discovery, full chain enumeration and real-data validation.
- [ ] Phase 7 — maximum-gap/opportunity engine, audit replay and advanced analytics.
- [ ] Phase 8 — walk-forward, robustness, Monte-Carlo and stress testing.
- [ ] Phase 9 — expand the same verified engine to BSE, stocks and commodities without duplicating the core.

## Current Implementation Checkpoints
### Historical storage
- `historical_catalog.py` — durable WAL-backed catalog, exact identity, duplicate/conflict protection and append/upcoming-data API.
- Tests verify history + upcoming append, duplicate suppression, late gap repair, Stock Future 1-second reuse and expiry isolation.

### Contract metadata
- `contract_master.py` — dated contract snapshots and expiry-aware resolution.
- `index_contracts.py` — dynamic index-future normalization across exchange segments; no hardcoded index-only universe.
- `angelone_contract_master.py` — Angel One instrument-master normalization for stock and index futures.
- `stock_future_lifecycle.py` — explicit start/end lifecycle and expiry-specific contract identity.

### Arbitrage
- `arbitrage_backtester.py` — executable Box Spread and Synthetic Cash–Carry pricing primitives with liquidity/quote validation.
- `arbitrage_scan_policy.py` — configurable ATM-relative strike-position policy:
  - Stock Box: 3/4/5 positions from ATM.
  - Index Box: 3–15 positions from ATM.
  - Stock Synthetic: both sides, max 5 positions.
  - Index Synthetic: both sides, max 15 positions.
- Current arbitrage primitives are implementation checkpoints; full production completion still requires dynamic universe/expiry enumeration, real historical data, execution simulation and end-to-end backtest validation.

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
- [ ] Box Spread scanner on liquid eligible option chains.
- [ ] Synthetic Future/Cash–Carry scanner on liquid stock/index F&O.
- [ ] Strategy-specific live data subscriptions; high-resolution only where required.
- [ ] Scanner result opens dedicated Live Analysis/P&L screen.
- [ ] Actual paper fills, quantity, average fill, live price and applicable charges drive paper P&L.
- [ ] Combined F&O strategy payoff graph with break-even, max profit/loss, profit/loss zones and live-price marker.
- [ ] Opportunity history query box: “ye kab hua tha?” with exact timestamp/contract/legs.
- [ ] Paper/live separation and reconciliation safeguards.

## Data Lifecycle — Core Rule
1. Startup checks/updates missing historical/off-market data.
2. Historical sync is normalized and deduplicated.
3. Live streaming starts only after the required startup gate.
4. Live streams operate during actual market hours; holidays/off-market periods stop unnecessary streaming.
5. Manual historical/off-market sync remains available.
6. Historical data is idempotent: same source + instrument + timeframe + timestamp (+ sequence where required) cannot create duplicates.
7. Required high-resolution live events are appended durably so future backtests can reuse them.
8. Gap repair merges into the same source dataset and never replaces valid history with a partial download.
9. No strategy is allowed to create its own duplicate market-data store when the required source data already exists.

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
- Generic high-resolution market-event model and replay foundation added.
- Generic event Strategy → Signal → Risk → Execution pipeline foundation added with deterministic tests.
- Durable historical append/gap-repair primitives added and tested.
- Dynamic index-future contract normalization added.
- Explicit stock-future expiry lifecycle primitives added.
- Executable Box Spread and Synthetic Cash–Carry backtesting primitives added.
- ATM-relative arbitrage strike-scan policy added and tested.

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
- [ ] Backtest result workspace: independent run selector, date range, resolution, strategy/version, contract/expiry and graph.
- [ ] Historical query box in every relevant result/analysis section.
- [ ] Arbitrage workspace: chain view, ATM marker, strike-position selector, legs, executable edge, payoff and opportunity history.
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
- Android build passes where the Android workflow is applicable.
- Relevant integration tests pass.
- Long-running paths have bounded memory.
- No credentials committed to Git.
- Data provenance/freshness is preserved where supported.
- UI remains responsive under scanner/backtest refresh.
- Backtest resolution is genuine and recorded; no fabricated higher-frequency data.
- Every backtest is independently reproducible and does not share mutable result state with another run.
- Contract/expiry/lot/tick identity is preserved.
- Graph/payoff/query outputs are traceable to the exact run ledger.
- Real live order routing remains disabled until safety, idempotency and reconciliation gates are explicitly verified.

## CI / Verification Checkpoint
- Latest previously verified **Build and Verify** success: commit `c1f92f55e6c06575b8827f8e84bd76444322762c` (fresh CI completed successfully).
- Latest arbitrage scan-policy implementation commit: `6298c0ca89f6c161a865d4df2c3c964621cc30c7`.
- Android verification for that arbitrage-policy commit completed successfully.
- The roadmap update itself is a new codebase checkpoint and therefore must receive a fresh CI run before this checkpoint is declared fully PASS.
- Never mark a feature/checkpoint PASS merely because code was committed; PASS requires the corresponding fresh CI result.

## Planned Execution Order — Autonomous
1. Finish/verify the current CI checkpoint.
2. Implement per-backtest genuine-resolution selection (`ms → s → m → h`) with coverage/provenance metadata.
3. Implement independent `BacktestRunSpec`/run result namespace and immutable strategy/version linkage.
4. Add graph/payoff result contracts and durable result storage.
5. Add historical section query API and exact event/trade/opportunity explainability.
6. Complete Index 1-second historical acquisition and coverage/gap download for all eligible contracts/expiries.
7. Validate Index 1-second replay with realistic execution assumptions.
8. Complete Box Spread dynamic universe/expiry/chain enumeration and historical backtesting.
9. Complete Synthetic Future/Cash–Carry dynamic universe/expiry/chain enumeration and historical backtesting.
10. Add maximum-gap/opportunity analytics and executable-vs-theoretical reporting.
11. Add Calendar Spread, rollover and advanced multi-leg execution simulation.
12. Add walk-forward, robustness, Monte-Carlo and stress testing.
13. Extend the verified core to stock futures, BSE and commodities without duplicating the engine/data layer.
14. Build live scanners, paper trading and mobile result/analysis screens on the same reusable data and strategy contracts.

## Development Rule
**One step → inspect → implement → test → fresh CI → verify PASS → update roadmap/checkpoint.**

No feature is considered complete until its code, tests, data assumptions and fresh CI verification agree with this roadmap.
