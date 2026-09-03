# Cash–Future Backtesting Roadmap

## Scope
- Strategy: Cash–Future arbitrage only.
- Backtest period: previous 1 year (365 days).
- Starting capital: ₹20,00,000.
- Resolution: 1-minute historical data.
- Universe: complete historically eligible F&O stock universe; do not use a manually selected stock list.
- Spot leg: NSE cash/equity data.
- Futures legs: current-month and near-month futures contracts, mapped by the historical replay date and actual contract expiry.

## Data & Synchronization
1. Ingest/import real historical NSE spot and F&O data.
2. Normalize timestamps to a single timezone and synchronize spot/future observations at 1-minute resolution.
3. Maintain historical contract identity, expiry, lot size, volume and OI.
4. Never invent missing historical data; unavailable data is reported explicitly.

## Opportunity Calculation
- Track headline gap: `future_price - spot_price`.
- Prefer executable prices: cash ask + futures bid for the entry direction, and the appropriate opposite executable prices for exit.
- Track bid/ask spread and reject opportunities that fail configured liquidity/execution checks.
- Calculate gross spread P&L, brokerage/statutory charges, funding cost, slippage and net P&L.

## Backtest Engine Rules
- No look-ahead bias.
- Signal and execution timing must be explicitly defined; do not execute using information unavailable at the signal timestamp.
- Use replay-date expiry/DTE, never `date.today()` for historical trades.
- Enforce ₹20 lakh available-capital and margin/deployment constraints.
- Support multiple eligible opportunities while preventing capital over-allocation.
- Handle contract expiry/rollover using historical contract mapping.

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

## Validation Gates
- [ ] Historical 1-minute data ingestion verified.
- [ ] Full eligible F&O universe coverage verified.
- [ ] Spot/current-month/near-month contract matching verified.
- [ ] Executable entry/exit pricing verified.
- [ ] Charges/funding/slippage verified.
- [ ] No-look-ahead tests verified.
- [ ] ₹20 lakh capital/margin constraints verified.
- [ ] Backtest report and equity curve verified.
- [ ] Paper-trading results can be compared against the same strategy logic.

## Current Priority
Build and verify the Cash–Future backtesting engine first. RSI, second scanner and real-money execution remain out of scope until this backtesting + paper-trading stage is validated.
