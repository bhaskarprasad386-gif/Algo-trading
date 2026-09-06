# Universal Advanced Backtesting Roadmap

## 1. Scope & Architecture
- Strategy-agnostic event-driven backtesting engine; Cash–Future is one adapter, not the engine boundary.
- Any strategy implementing the stable event-strategy contract must be backtestable.
- Targets: Cash–Future, futures, options, multi-leg options, calendar spreads, rollover, lead/lag, cross-instrument arbitrage and custom event strategies.
- Android/mobile-first UI; heavy backtests run in background workers and never block UI/API requests.
- Default paper-backtest capital: ₹1,00,00,000 (₹1 crore).
- GitHub remains the source of truth; every engine/data/model change is versioned and reproducible.

## 2. Universal Event & Time Engine
- Canonical timestamp: integer Unix epoch nanoseconds (`timestamp_ns`).
- Support tick, trade, quote, depth, millisecond, microsecond, second, minute, daily and other provider-native resolutions when actually available.
- Replay controls: 1us, 10us, 100us, 1ms, 10ms, 100ms, 1s, 5s, 15s, 1m, 5m, 15m, 1h, 1d and custom steps where valid.
- Preserve original source timestamps; never fabricate microsecond/tick observations from minute data.
- Deterministic same-timestamp ordering using sequence/source/event ordering.
- No-look-ahead: strategy history contains only observations already available before the decision; execution uses point-in-time market state.
- Higher-timeframe views are deterministic resamples of immutable source observations.

## 3. Historical Data Platform
- Persistent canonical historical store for raw and normalized observations.
- Coverage catalog by instrument, contract, date range, source/provider, version and checksum.
- Incremental ingestion: download only missing ranges/contracts.
- Daily append with timestamp + instrument/contract deduplication.
- Gap detection and targeted gap repair.
- Version-aware reconciliation when a provider revises historical data.
- Dataset lineage and immutable manifests for reproducibility.
- Data-quality scoring: completeness, duplicates, timestamp integrity, stale quotes, crossed/locked books, impossible prices and missing depth.
- Historical derivatives identity by underlying, expiry, strike, call/put, contract ID and lot size.
- Point-in-time option-chain membership and historically eligible F&O universe to prevent survivorship bias.
- Corporate actions, dividends, symbol changes, delistings and contract changes handled with historical effective dates.

## 4. Market Microstructure & Depth
- Quotes: bid/ask and sizes, spread and executable prices.
- Trades: last trade, size, volume and OI where available.
- Order-book depth: multiple bid/ask levels and displayed liquidity.
- Microstructure analytics: spread, quoted depth, executed volume, imbalance, microprice, VWAP/TWAP, trade intensity and liquidity scores.
- Circuit/price-band and session constraints where source data supports them.
- Missing/stale/crossed data is rejected or flagged, never silently invented.

## 5. Order & Execution Engine
### Completed foundation
- Point-in-time independent multi-leg execution.
- BUY uses executable ask and SELL uses executable bid when available.
- Depth execution consumes actual displayed levels and supports partial fills.
- No-liquidity conditions do not create synthetic fills.
- Order lifecycle foundation: submitted/accepted/partial/filled/cancelled/rejected/expired/replaced.
- Time-in-force foundation: DAY/GTC/IOC/FOK.
- Cancel/replace semantics and deterministic lifecycle tests.

### Next milestones
- Queue position and queue-delay model when sequence/depth evidence exists.
- Dynamic order-book depletion and cancel/reinsert behavior.
- STOP trigger lifecycle from observed market events.
- Bracket/OCO orders and trailing stops.
- Conditional orders and linked multi-leg order groups.
- Signal-to-submit, network, acknowledgement and fill latency model.
- Seed-controlled randomized latency stress.
- Market-impact models: participation/volume-share, temporary/permanent impact stress, separated from observed prices.
- Conservative execution model with explicit unsupported-data warnings.

## 6. Portfolio, Capital & Risk Engine
- Correct cash, market value, equity and realized/unrealized P&L accounting.
- ₹1 crore initial capital and configurable account constraints.
- Margin, leverage, notional and capital-locking enforcement.
- Prevent double allocation of the same capital/margin.
- Position concentration, sector/instrument exposure and correlated exposure controls.
- Max loss, drawdown, turnover and liquidity risk limits.
- Futures/options margin and collateral simulation.
- Portfolio margin / SPAN-like configurable model where historical inputs are available.
- Margin calls, blocked/released collateral and liquidation rules.
- Funding, financing, borrow costs, dividends and futures carry.

## 7. Strategy Layer
- Stable `EventStrategy` contract with version and strategy identity.
- Strategy parameters and configuration hash stored with every run.
- Strategy context includes point-in-time event/history, contract metadata, portfolio, open orders, capital, risk, fees, slippage and execution model.
- Strategy decisions emit explicit order type, quantity, price, TIF and execution constraints.
- Universal event-strategy lifecycle: start → event replay → end.
- Adapters to validate: Cash–Future, options multi-leg, futures, rollover, calendar spread, advanced arbitrage and at least two materially different strategies.

## 8. Options & Derivatives
- Point-in-time option-chain membership.
- Expiry/strike/call-put/contract identity and lot-size history.
- Multi-leg independent fills and residual/unhedged exposure.
- Greeks and volatility-surface inputs when source data supports them.
- Exercise/assignment/settlement rules where applicable.
- Futures rollover based on actual historical contract transitions, liquidity and execution—not today's contract map.
- Calendar/term-structure spread handling.

## 9. Session & Contract Calendar
- Historical trading calendar, holidays and special sessions.
- Pre-open, regular session, auctions, halts and instrument-specific sessions.
- Historical expiry/settlement timestamps.
- No `date.today()` for historical replay decisions.
- Cash/F&O contract transitions use replay-date availability only.

## 10. Durable Backtest Jobs & Performance
- Large jobs submitted asynchronously with immediate job ID.
- Durable states: queued, running, progress, completed, failed, cancelled and recoverable.
- Chunked/partitioned replay by date/symbol/contract with bounded memory.
- Incremental result persistence; completed results survive UI navigation/restart.
- Cancellation must not corrupt validated source data or completed results.
- Resource governor and bounded worker concurrency.
- Heavy workers isolated from login, scanner, paper trading, health and dashboard requests.
- Checkpoint/resume for long-running jobs.
- Safe parallel replay only where mathematically independent.
- Result caching for identical reproducible runs.

## 11. Audit, Explainability & Reproducibility
- Immutable run metadata: strategy ID/version/hash, engine version, dataset/version/checksum, parameters, calendar, cost/slippage/latency models and random seed.
- Audit trail for signals, orders, lifecycle transitions, fills, cancellations, rejections, risk blocks and exits.
- Per-leg timestamps, executable edge, hedge ratio, slippage and residual exposure for arbitrage.
- Every reported P&L value traceable to source observations and execution events.

## 12. Robustness & Validation
- Walk-forward optimization with untouched out-of-sample windows.
- Parameter sensitivity and stability regions.
- Regime-separated analysis: trend, volatility, low-volatility, gaps and stressed periods where identifiable.
- Cross-sectional analysis by symbol, sector, contract and liquidity bucket.
- Monte Carlo trade-order reshuffling and execution uncertainty.
- Bootstrap confidence ranges where statistically appropriate.
- Slippage, latency, fee and funding stress scenarios.
- Scenario engine and sensitivity analysis.
- Trade-count/statistical sufficiency warnings.
- Backtest vs forward-paper reconciliation.
- Robustness score is diagnostic, never a probability of future profit.

## 13. Reports & Exports
- Net P&L, ROI, drawdown, win rate, profit factor, turnover and trade count.
- Equity curve and monthly/yearly performance.
- Position, margin, capital utilization and liquidity usage.
- Per-symbol/sector/contract/expiry/regime performance.
- Full order lifecycle and fill ledger.
- Per-leg multi-leg execution report.
- Data coverage and data-quality report.
- Slippage/cost/latency stress report.
- Walk-forward/OOS/parameter/Monte Carlo reports.
- Downloadable durable backtest result datasets.

## 14. Validation Gates
- [x] Generic event model and nanosecond timestamp foundation.
- [x] Stable strategy contract and lifecycle foundation.
- [x] Point-in-time independent multi-leg execution.
- [x] Depth-aware multi-level execution foundation.
- [x] Partial-fill and no-liquidity behavior.
- [x] Order lifecycle foundation and deterministic tests.
- [x] DAY/GTC/IOC/FOK foundation and cancel/replace foundation.
- [ ] Queue-aware execution and dynamic depth.
- [ ] STOP/bracket/OCO/trailing lifecycle.
- [ ] Correct portfolio market-value/equity and cumulative P&L.
- [ ] Margin/capital/risk enforcement.
- [ ] Historical options/futures/rollover adapters.
- [ ] Persistent data catalog + incremental sync + gap repair.
- [ ] Point-in-time corporate actions and survivorship controls.
- [ ] Full-F&O background job pipeline and durable recovery.
- [ ] Microstructure analytics and liquidity scoring.
- [ ] Latency/impact stress models.
- [ ] Walk-forward/OOS/Monte Carlo/sensitivity validation.
- [ ] Full report/export pipeline.
- [ ] End-to-end validation with real historical datasets.

## 15. Execution Order From Here
1. Queue-aware execution + dynamic depth.
2. Portfolio/equity/P&L correction + margin/risk/capital locking.
3. Durable order/job/result ledger + checkpoint/resume.
4. Persistent historical catalog + incremental ingestion/dedup/gap repair.
5. Options/futures/rollover and advanced-arbitrage adapters.
6. Session/calendar/corporate-action/survivorship controls.
7. Microstructure, latency and market-impact models.
8. Full-F&O asynchronous pipeline and resource governor.
9. Reports, exports, robustness and OOS validation.
10. End-to-end real-data verification and Android/mobile integration.

## Non-Negotiable Data Rule
Architecture may support arbitrary timestamp precision, including microseconds, but a microsecond backtest is valid only when the verified source dataset actually contains microsecond-or-finer observations. Minute data must never be expanded into fake microsecond observations.
