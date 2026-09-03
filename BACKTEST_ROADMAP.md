# Cash–Future Backtesting Roadmap

## Scope
- Strategy: Cash–Future arbitrage only.
- Backtest period: previous 1 year (365 days).
- Starting capital: ₹20,00,000.
- Source resolution: 1-minute historical data.
- Universe: complete historically eligible F&O stock universe; do not use a manually selected stock list.
- Spot leg: NSE cash/equity data.
- Futures legs: current-month and near-month futures contracts, mapped by the historical replay date and actual contract expiry.

## Advanced Replay & Chart Interval Controls
The backtest keeps **1-minute data as the source of truth**, but the user can control how the historical replay advances and how the chart is displayed.

- Replay interval options: **1m, 5m, 10m, 15m, 30m, 1h, 1d**.
- When the user selects an interval, the backtest replay advances by exactly that interval.
- The chart uses the selected interval for displayed candles/points, while the underlying 1-minute observations remain available for drill-down and audit.
- Higher intervals are generated deterministically from the 1-minute source; no synthetic higher-timeframe source data is required.
- Switching intervals never changes the underlying backtest result or historical ledger.
- Minute-dependent calculations (execution, funding, MTM P&L, gap tracking and expiry) remain based on the 1-minute timeline.
- UI shows selected interval, replay timestamp, progress, and step forward/back controls.

## Data & Synchronization
1. Ingest/import real historical NSE spot and F&O data.
2. Normalize timestamps and synchronize Spot/current-month/near-month observations at 1-minute resolution.
3. Maintain historical contract identity, expiry, **lot size**, volume, OI and executable bid/ask where available.
4. Never invent missing historical data; unavailable data is reported explicitly.
5. Preserve the complete 1-minute timeline from entry until expiry/settlement for every active trade.

## Trade Replay Model
For a trade entered on a historical date such as 1 January:
1. Buy the Spot leg at the historical executable cash price.
2. Track the historical current-month Future short leg.
3. Track the historical near-month Future short leg.
4. Keep all three legs synchronized at every available 1-minute timestamp until trade completion/expiry.
5. At each minute calculate and store spot price, current-future price, near-future price, current and near gaps/basis, executable bid/ask, **contract lot size**, position value, charges, funding, mark-to-market P&L and cumulative net P&L.
6. Profit must be calculated from the actual historical gap and applicable lot size, with charges/funding/slippage included. The ledger must retain enough detail to explain how each interval's P&L was produced.
7. Preserve the full trade timeline so the same trade can later be replayed at 1m, 5m, 10m, 15m, 30m, 1h or 1d steps.

## Gap & Profit-by-Interval Analysis
- Every replay point must show: timestamp, spot price, current Future price, near Future price, current gap, near gap, executable gap where available, lot size, quantity/lots, gross spread value and net P&L.
- Gap-based profit must be shown **for every selected replay interval**, not only at entry/exit.
- The UI should show both per-interval P&L change and cumulative P&L.
- For a selected historical point, the system must be able to answer questions such as: **“Pichhle period mein itna gap kab mila tha?”**
- Historical gap lookup must return the exact historical date/time(s), symbol, contract/month, spot price, future price, near-future price, gap, executable gap, lot size, liquidity/execution status and the corresponding gross/net profit estimate.
- Lookup must respect the data available at that historical timestamp and must not use future information.
- Users should be able to search/filter by symbol, minimum/maximum gap, date range, contract month, lot size and executable status.
- If the same gap occurred multiple times, return ranked matches such as closest match, largest executable gap and most profitable historical occurrence, with timestamps.

## Expiry & Roll Handling
- Expiry is determined from the historical contract calendar for the replay date, never from today's date.
- If the current-month contract expires before the trade is completed, apply the configured historical rollover/settlement rule and continue tracking the appropriate historical contract.
- On expiry day, replay and valuation continue through the configured market close, with **3:30 PM** as the expiry-day cutoff for this strategy.
- Expiry/settlement automatically closes or settles the applicable leg(s) and calculates final realized P&L, charges and funding.
- Historical contract selection must not use future knowledge of contracts that were not yet available at that timestamp.

## Graphs & Advanced Analysis
- Main synchronized graph: Spot + current-month Future + near-month Future on the same time axis.
- Gap/basis graph: current-future minus spot and near-future minus spot, including executable gap where bid/ask data permits.
- P&L graph: minute-level mark-to-market and cumulative net P&L.
- Position/capital graph: deployed value, available capital, margin/funding and utilization.
- Chart intervals: **1m, 5m, 10m, 15m, 30m, 1h, 1d**.
- Selected interval changes displayed/replay granularity, not the underlying 1-minute source.
- Graph tooltips should expose lot size and gap-derived P&L for the selected timestamp/interval.
- 1d views are generated only from historical 1-minute observations within market sessions.

## Opportunity Calculation
- Track headline gap: `future_price - spot_price`.
- Prefer executable prices: cash ask + futures bid for the entry direction, and appropriate opposite executable prices for exit.
- Track bid/ask spread and reject opportunities that fail configured liquidity/execution checks.
- Calculate gross spread P&L, brokerage/statutory charges, funding cost, slippage and net P&L using the historical contract lot size and actual position quantity.

## Backtest Engine Rules
- No look-ahead bias.
- Signal and execution timing must be explicitly defined; do not execute using information unavailable at the signal timestamp.
- Minute-level ledger remains the canonical audit trail even when replay advances in larger intervals.
- Use replay-date expiry/DTE, never `date.today()` for historical trades.
- Enforce ₹20 lakh available-capital and margin/deployment constraints.
- Support multiple eligible opportunities while preventing capital over-allocation.
- Handle contract expiry/rollover using historical contract mapping.
- Historical gap-search results must be reproducible from the canonical minute ledger.

## Reports
- Net P&L
- ROI
- Maximum drawdown
- Win rate
- Profit factor
- Number of trades
- Equity curve
- Monthly and yearly performance
- Stock/contract-wise performance
- Expiry-wise performance
- Opportunity/rejection statistics
- Full minute-by-minute trade ledger
- **Lot-size and quantity-wise P&L**
- **Gap-wise and interval-wise P&L**
- **Historical gap occurrence/search report** with exact timestamps
- Replay/interval comparison: 1m vs 5m vs 10m vs 15m vs 30m vs 1h vs 1d

## Validation Gates
- [ ] Historical 1-minute data ingestion verified.
- [ ] Full eligible F&O universe coverage verified.
- [ ] Spot/current-month/near-month contract matching verified.
- [ ] Historical lot size mapping verified for every contract.
- [ ] Full minute-level trade ledger verified.
- [ ] Replay interval engine verified for 1m/5m/10m/15m/30m/1h/1d.
- [ ] Gap and lot-size based P&L verified at every replay interval.
- [ ] Historical gap lookup returns exact timestamps and reproducible values.
- [ ] Spot/current/near synchronized graphs verified.
- [ ] Executable entry/exit pricing verified.
- [ ] Charges/funding/slippage verified.
- [ ] No-look-ahead tests verified.
- [ ] ₹20 lakh capital/margin constraints verified.
- [ ] Expiry-day 3:30 PM handling verified.
- [ ] Automatic expiry/settlement and final P&L verified.
- [ ] Backtest report and equity curve verified.
- [ ] Paper-trading results can be compared against the same strategy logic.

## Current Priority
Build and verify the Cash–Future backtesting engine with the advanced replay/interval system, historical lot-size mapping, interval-wise gap/P&L ledger and historical gap-search capability first. RSI, second scanner and real-money execution remain out of scope until this backtesting + paper-trading stage is validated.
