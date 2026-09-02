# Changelog

## [0.1.0] - 2026-08-30

### Added
- Project foundation structure
- FastAPI backend with health endpoint
- Core config system (environment based)
- Modular packages:
  - market_data
  - scanners
  - backtesting
  - charts
  - algo
  - strategy_engine
- Basic logging
- .env.example
- README.md
- MASTER_SPEC.md
- Tick storage and candle-building foundation
- Candle persistence and market-data pipeline tests

### Notes
- Live order execution is disabled
- Scanner development not started
- Focus on clean and expandable base
- Strategy payoff auto-adjustment (Opstra style) will be handled in strategy_engine
- CI workflow includes push, pull_request, and manual workflow_dispatch verification

## [0.1.1] - 2026-09-01

### Verification
- Triggered a fresh CI verification checkpoint for the current market-data foundation.

## [0.1.2] - 2026-09-02

### Verified
- **Backtesting Engine foundation checkpoint VERIFIED.**
- Added deterministic backtest execution with separate entry/exit strategies, quantity, slippage, transaction cost, P&L, return, win rate and trade records.
- Added `backend/tests/test_backtesting.py` coverage for the backtesting foundation.
- Fixed floating-point-sensitive cost assertions with `pytest.approx()`.
- Fresh Backend Tests CI **#90** completed successfully: **67 tests passed**, 0 failed.
- **Backtesting max-drawdown enhancement VERIFIED.** `BacktestResult.max_drawdown` tracks realized peak-to-trough capital drawdown.
- Fresh Backend Tests CI **#97** completed successfully: **68 tests passed**, 0 failed.
- **Backtesting expectancy metric VERIFIED.** `BacktestResult.expectancy` reports average net P&L per completed trade.
- Fresh Backend Tests CI **#102** completed successfully: **68 tests passed**, 0 failed.
- **Backtesting Sharpe ratio metric VERIFIED.** `BacktestResult.sharpe_ratio` uses trade-level returns relative to initial capital, with zero risk-free rate and no annualization because the engine does not assume a fixed portfolio-return sampling frequency.
- Fresh Backend Tests CI **#106** completed successfully: **69 tests passed**, 0 failed.
- **Backtesting Sortino ratio metric VERIFIED.** `BacktestResult.sortino_ratio` uses trade-level returns relative to initial capital, zero minimum acceptable return and downside deviation without annualization.
- Fresh Backend Tests CI **#114** completed successfully: **70 tests passed**, 0 failed.
