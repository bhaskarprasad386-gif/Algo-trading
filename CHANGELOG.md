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
