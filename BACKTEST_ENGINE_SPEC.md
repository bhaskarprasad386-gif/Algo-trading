# Universal Backtesting Engine Specification

## Purpose
The backtesting engine is a **strategy-agnostic replay engine**. Cash–Future is one strategy implementation, not a limitation of the engine. The same engine must be able to backtest any supported strategy against the same canonical historical-data layer.

## Universal Strategy Support
- Any supported strategy must be backtestable through a stable strategy plugin/interface; the core engine must not contain strategy-specific assumptions.
- Strategies may include cash/equity, futures, options, spreads, arbitrage, statistical/pairs, directional, market-neutral, portfolio, multi-leg and custom strategies, subject to available historical data.
- Explicit first-class examples: **Cash–Future arbitrage, Options strategies, Futures strategies, Future rollover/rollover arbitrage, calendar spreads, advance/lead-lag arbitrage, cross-instrument arbitrage, and multi-leg combinations**.
- A strategy can consume one or many instruments and multiple synchronized market feeds.
- Strategy parameters, code/version/hash and configuration are versioned and stored with every run.
- Entry, exit, position sizing, portfolio rules, risk rules, order generation and custom indicators are strategy-defined.
- Multiple strategies can run against the same historical dataset without downloading the dataset again.

## Time Resolution: Arbitrary Precision
- The canonical event timestamp is stored as an **integer Unix epoch in nanoseconds (`timestamp_ns`)**. This permits deterministic ordering for microsecond data and leaves room for finer-than-microsecond source timestamps.
- The engine accepts any source resolution for which real historical data exists: tick, second, millisecond, microsecond, daily, etc.
- Supported replay step is not hard-coded to 1-minute intervals. A run may specify `1us`, `10us`, `100us`, `1ms`, `10ms`, `100ms`, `1s`, `5s`, `15s`, `1m`, `5m`, `15m`, `1h`, `1d`, or a custom duration.
- The engine must preserve the original source event timestamp; replay/resampling must never overwrite source precision.
- If a provider only supplies 1-minute data, the engine must **not manufacture microsecond events**. It must report the highest verified source resolution available.
- Multiple events sharing the same timestamp are ordered deterministically using sequence/source-event identity; timestamp collisions must never be silently discarded.
- Timezone, exchange calendar and session metadata are stored with the dataset/run.

## Canonical Historical Data Store
- Store raw/source observations and normalized observations with source, provider, dataset version and checksum metadata.
- Primary event identity includes instrument/contract identity + `timestamp_ns` + event/sequence identity where required.
- Enforce database uniqueness so repeated downloads/imports cannot create duplicates.
- Preserve tick/order-book/quote/trade fields when supplied by the source instead of reducing everything to OHLC.
- Keep bid, ask, bid/ask sizes, last trade, volume, OI, depth and other microstructure fields when available.
- Maintain a coverage catalog by instrument, contract, date/time range, resolution, source and data version.
- Incremental synchronization downloads only missing ranges/events.
- Gap repair downloads only missing/corrupt ranges.
- Source revisions are version-aware so historical backtests remain reproducible.
- The engine reads the persisted canonical store in bounded streams/chunks; it must not load a full-year/full-universe dataset into RAM.

## Derivatives / Rollover / Arbitrage Data Model
- Historical futures contracts are identified point-in-time by underlying, expiry, contract month, lot size and exchange contract identity.
- Rollover strategies must replay the **actual historical contract transition**, not a modern contract list applied retrospectively.
- Support current-month, near-month, far-month and custom contract selection rules.
- Support options by expiry, strike, call/put, contract identity, lot size and point-in-time chain membership.
- Support multi-leg spreads and arbitrage legs with synchronized timestamps and independent executable prices.
- Support advance/lead-lag arbitrage where the strategy defines the relationship and the required historical feeds exist.
- Rollover/roll arbitrage must account for expiry, settlement, last-trading-session rules, liquidity, bid/ask, costs, funding/margin and leg execution timing.

## Event Replay Pipeline
1. Resolve requested dataset coverage from the local catalog.
2. Fetch only missing verified-source ranges when required.
3. Validate ordering, duplicates, timestamps, price sanity, session/calendar and instrument identity.
4. Stream events in deterministic chronological order across all required instruments/legs.
5. Pass each event to the selected strategy through a stable market-data context containing only point-in-time information.
6. Convert strategy decisions into orders/legs.
7. Apply the configured historical execution/fill model independently to each leg where required.
8. Update portfolio, cash, margin, fees, funding and risk state.
9. Persist the result ledger incrementally.
10. Produce reproducible summary metrics from the persisted ledger.

## Strategy Interface Requirements
Every strategy must be able to receive:
- Current event timestamp and prior point-in-time history only.
- One or many synchronized instrument streams.
- Market/trade/quote/order-book events as available.
- Historical contract metadata valid at that timestamp.
- Portfolio state, open orders and available capital.
- Configured risk, fees, slippage and execution models.

Every strategy must be able to emit:
- Buy/sell/short/cover/order actions where the market supports them.
- Multi-leg orders and relationships between legs.
- Order type, quantity, price/limit, time-in-force and execution constraints.
- Stop-loss/take-profit/trailing/custom exit instructions.
- Strategy metadata/reason codes for auditability.

## Execution Realism
- Separate signal timestamp from order submission and fill timestamp.
- Support market, limit, stop and other supported order types through an execution adapter.
- Support configurable latency, queue delay, bid/ask crossing, partial fills, liquidity limits, rejection and cancellation.
- For arbitrage/multi-leg strategies, model leg-by-leg fills and residual/unhedged exposure rather than assuming all legs fill simultaneously.
- Never fill at a price that was not available under the configured point-in-time execution model.
- Preserve every order state transition in the ledger.
- Support normal/conservative/severe slippage and cost-stress profiles.

## Portfolio & Capital
- Default paper-backtest starting capital: **₹1,00,00,000 (₹1 crore)**, configurable per run.
- Capital, margin, leverage, funding and collateral rules are configurable by strategy/market.
- Prevent capital or margin from being allocated twice.
- Track realized/unrealized P&L, fees, funding, cash, margin and equity at every source event/replay point required by the strategy.
- Support multiple simultaneous positions, instruments and strategy legs.

## Resampling / Timeframe Views
- Source events remain immutable.
- Higher timeframes are deterministically generated from the underlying source events.
- A strategy may explicitly request its native timeframe while the chart/report may use another display interval.
- Switching chart/replay interval must not silently change the canonical source ledger.
- For microsecond/millisecond strategies, reports can retain event-level data; for very large datasets, results are paged/streamed rather than held in memory.

## Reproducibility and Anti-Lookahead
Each backtest run stores:
- strategy ID/name and immutable strategy version/hash
- complete parameter set
- dataset/source IDs and data-version hashes
- requested and verified source resolution
- execution/fill model version
- fee/funding/slippage configuration
- trading calendar/session version
- engine version/commit
- starting capital and risk configuration

The engine must enforce point-in-time access. No strategy callback may access future events, future contract metadata or future revised information that was unavailable at the replay timestamp.

## Durable Result Storage
Persist incrementally:
- run/job metadata and status
- event/replay timestamp
- strategy signal/decision
- orders and lifecycle transitions
- fills and execution details for every leg
- positions and quantities
- cash/equity/margin
- gross/net P&L
- fees/funding/slippage
- strategy-specific audit fields

Completed records must survive UI navigation, process restart and partial job failure where the storage backend supports recovery. A resumed job must continue from durable state without corrupting already persisted results.

## Background Execution
- Large backtests run as durable asynchronous jobs.
- API returns a job ID instead of blocking on computation.
- Progress includes status, processed events, date/time range, instruments, trades/orders and runtime/resource information.
- Worker count, memory, database connections and storage usage are bounded.
- Heavy replay must not block login, scanner, paper trading, health checks or dashboard requests.
- Cancellation is cooperative and must leave the canonical market-data store intact.

## Reports
The universal engine must expose common metrics including net P&L, ROI, drawdown, win rate, profit factor, trade/order count, equity curve, exposure, capital utilization, fees, slippage, funding and execution statistics. Strategies may add custom metrics. Arbitrage reports must additionally show per-leg entry/exit/fill timestamps, spread/edge, hedge ratio, leg slippage and residual exposure. Rollover reports must show contract transitions and roll costs.

## Advanced Institutional-Grade Extensions
- **Market microstructure analytics:** spread, quoted depth, executed volume, order-book imbalance, microprice, VWAP/TWAP, trade intensity, volatility regimes and liquidity scores when source data supports them.
- **Queue-aware execution:** configurable queue position, queue delay, order-book depletion and cancel/reinsert behavior; never assume queue information that the dataset cannot support.
- **Full order lifecycle:** pending, accepted, partially filled, filled, cancelled, rejected, expired and replaced states with deterministic IDs and timestamps.
- **Advanced order types:** IOC, FOK, DAY/GTC, bracket/OCO, trailing stop and configurable conditional orders where historically simulatable.
- **Market impact models:** participation-rate/volume-share, spread impact and configurable temporary/permanent impact stress models, clearly separated from observed historical prices.
- **Latency model:** signal-to-submit, network, exchange acknowledgement and fill latency as separate configurable components, including randomized but seed-controlled latency for stress testing.
- **Risk engine:** pre-trade and intraday limits for notional, leverage, margin, concentration, sector/instrument exposure, max loss, drawdown, turnover, liquidity and correlated exposure.
- **Portfolio margin simulation:** strategy/contract-specific SPAN-like or configurable margin schedules, collateral, blocked capital, released margin and margin-call/liquidation events where data/rules are available.
- **Funding/carry model:** financing, borrow cost, dividend/corporate-action adjustments, futures basis/carry and option funding effects where applicable.
- **Corporate-action aware history:** splits, bonuses, dividends, symbol changes, delistings and contract changes with point-in-time effective dates and adjustment policy.
- **Survivorship-bias protection:** universe membership must be point-in-time, including delisted/expired instruments where historical data exists.
- **Session/micro-session engine:** exchange holidays, special sessions, auctions, pre-open/post-close, trading halts, circuit limits and instrument-specific trading calendars.
- **Data-quality scoring:** completeness, duplicate rate, timestamp integrity, crossed/locked quotes, impossible prices, stale quotes and missing-depth diagnostics before a run is allowed.
- **Dataset lineage:** immutable dataset manifests, checksums, provider/version IDs and coverage fingerprints so two runs can prove whether they used identical data.
- **Deterministic randomness:** every stochastic model uses an explicit seed stored in the run manifest; repeated runs with identical inputs must reproduce identical results.
- **Walk-forward optimization:** rolling train/validation/test windows with strict temporal separation and saved parameter-selection history.
- **Out-of-sample validation:** holdout periods, regime-separated tests and parameter-stability analysis to reduce overfitting.
- **Monte Carlo robustness:** trade-order reshuffling, slippage/latency perturbation, execution uncertainty and bootstrap-style stress scenarios with reproducible seeds.
- **Sensitivity analysis:** automatic parameter sweeps showing P&L/drawdown/turnover stability rather than selecting only the single best point.
- **Regime analysis:** classify results by volatility, trend, liquidity, market direction and event regime when supported by available data.
- **Scenario engine:** configurable fee, spread, latency, slippage, liquidity, gap and capital shocks without modifying the canonical historical dataset.
- **Cross-strategy portfolio replay:** multiple independent strategies can share one capital/risk engine to measure correlation, capital contention and combined drawdown.
- **Strategy sandbox/versioning:** strategy code/configuration hash, dependency/environment fingerprint and explicit engine compatibility version stored for every run.
- **Explainability/audit trail:** each signal, order, fill, rejection, risk block and exit stores a machine-readable reason code plus relevant point-in-time inputs.
- **Checkpoint/resume:** periodic durable checkpoints allow a large replay to resume from the last committed event boundary after process failure.
- **Parallel replay:** partition by independent datasets/date ranges where mathematically safe, with deterministic merge of results; shared-capital strategies must remain ordered.
- **Result caching:** identical dataset + strategy hash + parameters + engine/execution/risk configuration may reuse a verified prior result instead of recomputing.
- **Resource governor:** CPU, RAM, disk, database and worker limits with backpressure so large F&O/microstructure runs cannot destabilize the Android/API service.
- **Data licensing/source controls:** source/provider metadata and availability are visible so the engine never implies that unavailable or unlicensed historical precision exists.

## Validation Requirements for Advanced Extensions
- No-lookahead and point-in-time contract/universe membership tests.
- Independent multi-instrument bid/ask/depth execution tests.
- Queue/partial-fill/cancel/replace lifecycle tests.
- Risk rejection and margin-call/liquidation tests.
- Corporate-action and survivorship-bias tests where supported.
- Data-quality gate tests for duplicates, gaps, invalid quotes and timestamp collisions.
- Deterministic seed/reproducibility tests for stochastic execution and Monte Carlo modules.
- Walk-forward/holdout tests that prove future data cannot influence training windows.
- Scenario/sensitivity tests proving canonical source data remains unchanged.
- Checkpoint/resume equivalence tests: resumed run must match uninterrupted run.
- Resource-bound tests for bounded-memory streaming and background execution.

## Data Availability Rule
"Any timeframe" means the engine architecture supports arbitrary timestamp precision; it does **not** mean the system invents unavailable historical microsecond data. A microsecond backtest is valid only when the configured provider/import contains verified microsecond-or-finer observations for the requested instruments and period. Coverage must be shown before execution. Options, futures rollover and advanced arbitrage likewise require the corresponding historical contracts/quotes/trades/order-book data to actually exist in the canonical store.

## Validation Gates
- [ ] Generic strategy interface verified with at least two materially different strategies.
- [ ] Cash–Future, options, futures rollover and multi-leg arbitrage strategy adapters covered by engine-level tests.
- [ ] Same dataset reused by multiple strategies without duplicate downloads.
- [ ] Microsecond timestamp storage/order verified with `timestamp_ns`.
- [ ] Millisecond/second/minute/hour/day replay-step tests verified.
- [ ] Same-timestamp event ordering verified.
- [ ] No synthetic microsecond data generated from minute candles.
- [ ] Incremental historical ingestion and duplicate prevention verified.
- [ ] Gap detection/repair verified.
- [ ] Point-in-time/no-lookahead tests verified.
- [ ] Durable event/order/fill/portfolio ledger verified.
- [ ] Multi-leg independent fill and residual-exposure tests verified.
- [ ] Historical expiry/rollover mapping verified.
- [ ] ₹1 crore default paper capital verified.
- [ ] Background job/progress/cancellation/recovery verified.
- [ ] Repeated identical backtest is reproducible from the canonical dataset and stored run configuration.
- [ ] Advanced market-impact, queue, risk, margin, data-quality, walk-forward, Monte Carlo, scenario and checkpoint modules are individually testable and cannot alter canonical source observations.
