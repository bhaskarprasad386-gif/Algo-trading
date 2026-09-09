# Universal Advanced Backtesting Roadmap

## Final Strategy Scope (Locked)
- **Calendar Spread:** all supported F&O contracts across **NSE + BSE + supported commodity derivatives**, covering eligible stocks and indexes. Use actual near/far historical expiries and contract identity; CE/PE and futures calendars only where structurally valid and genuine historical data exists.
- **Box Spread:** NIFTY 50 stocks use **5 actual strike positions on each side of ATM (5 CE/PE positions below + 5 above)**. Index Box uses **3 through 15 actual strike positions from ATM**, for both **genuine weekly and monthly expiries** where historically available.
- **Synthetic Future / Synthetic Cash-Carry:** only **liquid supported stocks and indexes**. Stocks use both sides up to **5 actual strike positions**; indexes use both sides up to **15 actual strike positions**. Use Call + Put + Future legs where structurally applicable.
- Strike counts always mean **ordered positions in the point-in-time option chain**, never rupee-distance.
- All strategies use actual historical contracts, expiry, bid/ask/depth, liquidity, lot size, fees, slippage and executable fills. Missing genuine data is reported as a coverage gap and never fabricated.

## 1. Final Scope
- Universal, strategy-agnostic, event-driven engine; `EventStrategy` is the stable extension boundary.
- Exchanges: **NSE + BSE + supported commodity exchanges/contracts** where genuine historical data exists.
- Instruments: cash/equity, stock futures, index futures, stock options, index options, commodity futures/options where supported, and future instrument types without a new engine/schema.
- Strategies: Cash–Future, Synthetic Cash-Carry, Calendar Spread, Box Spread, rollover, lead/lag, cross-instrument arbitrage and arbitrary event/tick/news strategies.
- Android/mobile-first UI; heavy jobs run asynchronously in bounded background workers.
- Default paper capital: ₹1,00,00,000.
- GitHub is the source of truth; every change is versioned and reproducible.

## 2. Independent Backtest Run
Every run gets an independent Run ID and immutable metadata:
- strategy ID/version + parameter/configuration hash
- requested/effective date range
- exchange, underlying, contract ID, expiry, strike, CE/PE, lot size
- source/provider, dataset version/checksum, resolution and coverage/watermark
- calendar/session version
- fees, funding, slippage, latency, execution model and random seed
- engine/build version and provenance

## 3. Universal Time/Data Resolution
- Canonical `timestamp_ns`.
- Genuine tick/trade/quote/depth, microsecond, millisecond, second, minute, hour, daily and provider-native resolutions when available.
- Each run selects the **finest genuine complete verified dataset** applicable to the requested range/instrument.
- If finer data is incomplete, fall back to the finest complete valid source and record that decision.
- Never manufacture microsecond/second/ms observations from minute candles.
- Preserve source timestamps and deterministic same-timestamp sequence/source ordering.
- No look-ahead; historical decisions use only information available at that time.

## 4. Historical Data Platform
- Persistent canonical raw/normalized store + coverage catalog.
- Coverage tracked by exchange/instrument/contract/date/source/resolution/version/checksum.
- Incremental download/append of only missing ranges/contracts.
- Timestamp + contract + sequence-aware deduplication.
- Targeted gap detection and gap repair only for missing portions.
- Provider-revision reconciliation, immutable manifests and lineage.
- Data-quality checks: completeness, duplicates, timestamps, stale/crossed books, impossible prices and missing depth.
- Point-in-time derivative identity and F&O universe/option-chain membership; no survivorship leakage.
- Corporate actions, dividends, symbol changes, delistings and contract changes with effective dates.

## 5. Market Microstructure & Execution
- Historical bid/ask + sizes, OI, volume, spread and executable prices where available.
- Multi-level depth where available.
- Independent point-in-time execution for every leg.
- BUY uses ask; SELL uses bid; depth consumes actual displayed liquidity.
- Partial fills and residual/unhedged exposure are real ledger states.
- No-liquidity conditions never create synthetic fills.
- Fees, taxes/brokerage, funding/carry and slippage are separate and traceable.
- Future milestones: queue position, dynamic depth depletion, latency, STOP/bracket/OCO/trailing orders and market-impact stress.

## 6. Portfolio / Margin / Risk
- Correct cash, market value, equity, realized/unrealized and cumulative P&L.
- ₹1 crore default capital, configurable per run.
- Margin, leverage, notional and capital locking; prevent double allocation.
- Futures/options/commodity margin and collateral where historical inputs support it.
- Margin calls, blocked/released collateral and forced liquidation.
- Funding, borrow, dividends and futures carry.
- Max loss, drawdown, turnover, concentration and liquidity controls.

## 7. Universal Strategy Layer
- Stable lifecycle: `start → event replay → end`.
- Strategy context: point-in-time history/events, contract metadata, portfolio, orders, capital, risk, fees, slippage and execution model.
- Explicit order outputs: side, quantity, price, TIF and constraints.
- New strategies use the same engine/result schema; **no new database/schema per strategy**.

## 8. Four Core Arbitrage Adapters
### Cash–Future
- Actual cash/spot + historical future contract identity.
- Point-in-time current/near/further contract selection.
- Executable bid/ask, carry/funding/fees/slippage and real reverse exit.

### Synthetic Cash-Carry
- **Liquid supported stocks and indexes only.**
- Stock: both sides up to **5 actual option-chain positions from ATM**.
- Index: both sides up to **15 actual option-chain positions from ATM**.
- Actual Call + Put + Future legs where applicable.
- Historical strike/expiry/contract identity; independent fills and real reverse exit.
- Complete three-leg payoff/P&L; no fabricated exit.

### Calendar Spread
- **All supported F&O stocks and indexes across NSE + BSE + supported commodity derivatives.**
- Near/far actual historical expiries; CE/PE where applicable and futures calendar where structurally valid.
- Never mix expiry identity; preserve exchange/symbol/expiry/strike/contract/lot metadata.
- Independent bid/ask/depth execution and real reverse exit only.
- Generic index support; not hard-coded to specific index names.

### Box Spread
- **NIFTY 50 stocks:** exactly **5 actual strike positions below ATM + 5 actual strike positions above ATM**.
- **Indexes:** **3 through 15 actual strike positions from ATM**, for **weekly and monthly** expiries where genuine historical weekly/monthly contracts exist.
- Actual CE/PE bid/ask/depth and four-leg execution.
- Expiry/strike/contract identity preserved; payoff/P&L only from executable legs.

## 9. Strike Rules (Locked)
- Stock Box: NIFTY 50 stocks, **5 positions each side of ATM**.
- Stock Synthetic: liquid supported stocks, **up to 5 positions each side of ATM**.
- Index Box: **3–15 positions from ATM**, separately for genuine weekly and monthly expiries.
- Index Synthetic: liquid supported indexes, **up to 15 positions each side of ATM**.
- Position = ordered strike position in the point-in-time chain, never a rupee-distance approximation.
- Missing strike/leg/chain data is a recorded coverage gap, never silently filled.

## 10. Expiry / Session / Contract Lifecycle
- Historical holidays, sessions, pre-open/auction/halts where supported.
- Historical expiry and settlement timestamps.
- Monthly expiry and genuine historical weekly index expiry where source contains it.
- Expiry-to-expiry lifecycle and rollover from actual historical contract transitions/liquidity/execution.
- No `date.today()` or today's contract map in historical replay.

## 11. Payoff / Analytics
For every completed applicable run:
- payoff curve + graph-ready data
- break-even, max profit, max loss, expiry payoff
- equity/P&L curve and drawdown
- trade-by-trade and per-leg execution
- residual exposure, net P&L after fees/slippage/funding
- existing generic payoff engine remains the common layer

## 12. Query / Explainability
Every result is traceable:
**date → exact timestamp → exchange → underlying → contract/expiry → strikes → legs → prices → resolution → orders/fills → costs → P&L**.

A query such as “opportunity कब आई थी?” must return the exact source observations and execution events supporting it.

## 13. Incremental Result Ledger
- Full-year/full-F&O result must never require the whole ledger in RAM.
- SQLite/WAL durable incremental persistence for trades, events, orders/fills, equity and metadata.
- Cursor/chunk reads for UI/reports.
- Completed results survive restart/navigation.
- Checkpoint/resume and cancellation-safe writes.

## 14. Durable Jobs / Performance
- Immediate Job ID + Run ID.
- States: queued/running/progress/completed/failed/cancelled/recoverable.
- Chunked replay by date/symbol/contract with bounded memory.
- Resource governor and bounded concurrency.
- Heavy workers isolated from login/scanner/paper-trading/dashboard.
- Idempotent retry/resume and reproducible-result caching where safe.

## 15. Audit / Reproducibility
- Immutable run/dataset manifests.
- Audit trail for signals, orders, fills, cancels, rejects, risk blocks and exits.
- Per-leg timestamps, executable edge, hedge ratio, slippage and residual exposure.
- Every P&L value traceable to source observations and execution events.

## 16. Robustness / Reports
- Walk-forward + untouched OOS.
- Parameter sensitivity/stability, regime and cross-sectional analysis.
- Monte Carlo/bootstrapping where statistically appropriate.
- Fee/slippage/funding/latency stress.
- Backtest vs forward-paper reconciliation.
- Reports: P&L, ROI, drawdown, win rate, profit factor, turnover, trade count, margin/capital usage, per-symbol/contract/expiry results, full fill ledger, data-quality/coverage and stress reports.

## 17. Validation Status
### Completed foundation
- [x] Generic event model + nanosecond timestamp.
- [x] Stable strategy contract/lifecycle.
- [x] Point-in-time independent multi-leg execution.
- [x] Depth-aware execution foundation.
- [x] Partial-fill/no-liquidity behavior.
- [x] Order lifecycle + DAY/GTC/IOC/FOK + cancel/replace foundation.
- [x] Persistent historical catalog foundation.
- [x] Exact-timestamp catalog replay with completeness filtering.
- [x] Four arbitrage adapters registered and catalog-backed tests.
- [x] Catalog→ledger E2E tests for Cash–Future, Synthetic, Calendar and Box.
- [x] Missing-exit safety: no fabricated trade/P&L.

### Remaining gates
- [ ] Queue-aware execution + dynamic depth.
- [ ] Portfolio/equity/cumulative P&L correction.
- [ ] Margin/risk/capital locking + liquidation validation.
- [ ] Production-scale real historical acquisition for supported NSE/BSE/commodity instruments.
- [ ] Incremental sync + targeted gap repair at production scale.
- [ ] Historical options/futures/rollover coverage across supported exchanges.
- [ ] Explicit genuine-data NSE/BSE/index/commodity Calendar validation.
- [ ] Explicit NIFTY 50 stock Box 5+5 validation.
- [ ] Explicit index Box weekly/monthly 3–15 validation.
- [ ] Explicit liquid-stock Synthetic ±5 and index Synthetic ±15 validation.
- [ ] Corporate actions/survivorship controls.
- [ ] Durable full-F&O job/recovery pipeline.
- [ ] Microstructure/liquidity analytics.
- [ ] Latency/market-impact stress.
- [ ] Walk-forward/OOS/Monte Carlo/sensitivity validation.
- [ ] Full report/export pipeline.
- [ ] Real-data end-to-end P&L reconciliation.
- [ ] Android/mobile production integration.

## 18. Execution Order From Current State
1. Queue-aware execution + dynamic depth.
2. Portfolio/equity/P&L + margin/risk/capital locking.
3. Durable order/job/result ledger + checkpoint/resume hardening.
4. Historical catalog production ingestion + dedup/gap repair.
5. Options/futures/rollover + NSE/BSE/index/commodity arbitrage coverage.
6. Explicit Calendar/Box/Synthetic universe and strike-rule validation against genuine historical chains.
7. Session/calendar/corporate-action/survivorship controls.
8. Microstructure + latency + market impact.
9. Full-F&O async pipeline + resource governor.
10. Reports/exports + robustness/OOS validation.
11. Real-data E2E: **Code → Compile → Tests → Fresh CI → PASS → Next**.
12. Android/mobile integration + production validation.

## 19. Non-Negotiable Rules
1. No fabricated market data.
2. No fabricated execution or exits.
3. No expiry/contract mixing.
4. No survivorship leakage.
5. No future/look-ahead leakage.
6. Strike rules use actual chain position counts, never ₹ gap.
7. Box Stock = NIFTY 50, 5 strikes each side; Index = 3–15, weekly + monthly where genuine.
8. Synthetic Stock = liquid stocks ±5; Index = ±15.
9. Calendar covers all supported F&O stocks/indexes across NSE/BSE and supported commodities.
10. Yearly/F&O ledger is incremental, never RAM-only.
11. New strategies do not require new schemas.
12. Never report CI PASS without actual evidence.
13. Continue milestone execution without waiting for user confirmation.
