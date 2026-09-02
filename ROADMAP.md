# Algo Trading — Master Roadmap

## Vision
Fast, modular, mobile-first advanced F&O algo-trading platform. GitHub is the source of truth. Build small verified checkpoints; no large untested dumps.

## Performance & Architecture
- Python for orchestration/data/backtesting; C++ fast core when justified.
- Android native/Kotlin + mobile-friendly web dashboard; Flutter/React Native remain future alternatives.
- WebSocket live market stream; broker API (Angel One SmartAPI) rather than scraping.
- In-memory processing; Redis when scale requires it.
- Protobuf/FlatBuffers zero-copy boundary where useful.
- Target ~10–15 ms software decision/processing path; never treat this as guaranteed exchange execution latency.
- Future scale: hot-standby, Raft/Paxos, FPGA, co-location/DMA.
- Start free/low-cost infrastructure; allow migration to paid low-latency servers without redesign.

## Data Lifecycle — Core Rule
1. Data ON/startup first checks and updates missing historical/off-market data.
2. Historical sync completes and data is normalized/deduplicated.
3. Only then is live streaming enabled.
4. Live data runs only during actual market hours; holidays/off-market periods stop live streaming.
5. Historical/off-market updates remain available when manually starting a sync outside market hours.
6. Historical data must be idempotent: same token + timeframe + timestamp must not create duplicates.

## Verified Foundation So Far
- Backend/app foundation and health checks.
- Existing Angel One market-data client/provider boundary.
- Historical provider aligned with existing client contract.
- Historical → live startup gate: live blocked until historical sync is complete.
- Stable candle normalization layer.
- **Candle storage identity checkpoint VERIFIED:** canonical `app.models.candle.Candle` now enforces a unique `(token, timeframe, timestamp)` database index; existing `CandleStorage.save()` performs timestamp-keyed update/upsert semantics; fresh Backend Tests CI run #82 passed all 62 tests.
- **Strategy Builder foundation checkpoint VERIFIED:** commit `6e0107b` added deterministic strategy/rule composition primitives in `backend/app/algo/strategy.py` plus `backend/app/algo/__init__.py` and `backend/tests/test_strategy.py`; fresh Backend Tests CI run #84 passed all 64 tests.
- **Backtesting Engine foundation checkpoint VERIFIED:** deterministic entry/exit execution with quantity, slippage, transaction cost, P&L, return, win rate and trade records is implemented in `backend/app/backtesting/engine.py` with tests in `backend/tests/test_backtesting.py`; fresh Backend Tests CI run #90 completed successfully with **67 tests passed**.
- **Backtesting max-drawdown enhancement VERIFIED:** `BacktestResult.max_drawdown` tracks realized peak-to-trough capital drawdown; fresh Backend Tests CI run #97 completed successfully with **68 tests passed, 0 failed**.
- **Backtesting expectancy metric VERIFIED:** `BacktestResult.expectancy` reports average net P&L per completed trade; fresh Backend Tests CI run #102 completed successfully with **68 tests passed, 0 failed**.
- **Backtesting Sharpe ratio metric VERIFIED:** `BacktestResult.sharpe_ratio` uses trade-level returns relative to initial capital, with zero risk-free rate and no annualization because the engine does not assume a fixed portfolio-return sampling frequency; fresh Backend Tests CI run #106 completed successfully with **69 tests passed, 0 failed**.
- **Backtesting Sortino ratio metric VERIFIED:** `BacktestResult.sortino_ratio` uses trade-level returns relative to initial capital, zero minimum acceptable return and downside deviation without annualization; fresh Backend Tests CI run #114 completed successfully with **70 tests passed, 0 failed**.
- CI workflow supports push/pull_request/manual verification.

## Core Trading Engines
- Strategy builder with indicator/custom-rule composition.
- Backtesting engine with historical data, realistic charges/slippage and performance metrics.
- Paper trading + live trading dual-engine with synchronized signals and separate fills/state.
- Auto-adjust SL/target based on actual paper/live entry prices.
- Human-in-the-loop confirmation gateway with TTL for live orders.
- Idempotency keys, smart retry/fallback, pre-trade margin/risk/circuit checks.
- Kill switch, daily loss limits, error circuit breaker.
- Multi-leg execution safeguards, partial-fill monitoring, watchdog and rollback/square-off logic (latency thresholds must be broker/API-realistic, not assumed).
- ATR + India VIX dynamic position sizing.
- Smart limit pegging/slippage protection.
- Net-profit engine including brokerage, STT, GST and other applicable costs.

## Scanner & Professional Metrics
- Nifty 50/100/500 and F&O scanners.
- RSI, OBV, SMA, consolidation/sideways/fall patterns, bullish divergence/Wyckoff spring.
- Spot vs futures, cash-and-carry, put-call parity and other arbitrage scanners.
- Options liquidity: volume, OI, bid/ask spread, executable edge.
- GEX, Greeks, delta-neutral monitoring and dynamic hedging.
- Order Book Imbalance/depth and large/iceberg-liquidity detection where reliable market-depth data is available.
- Block-deal/large-trade monitoring; distinguish exchange-visible block trades from unavailable/unsupported true dark-pool data.
- SLB/short-interest, promoter pledge/unpledge and insider/disclosure metrics where lawful/public data is available.
- Sector strength, FII/DII, broker actions and cross-asset divergence.

## Macro Dashboard
- Nifty/Bank Nifty spot vs futures.
- RBI Repo/SDF and policy events.
- India Manufacturing/Services PMI.
- DXY, US 10Y Treasury yield.
- Brent/WTI and inventories.
- TREPS/MIBOR/SOFR and macro-liquidity indicators where data is available.
- Cross-asset relationships: equity, bonds, INR/DXY, crude, gold.

## Quant / Institutional Strategies
- Statistical arbitrage and pairs trading.
- Cross-sectional momentum.
- Volatility risk-premium strategies with strict tail-risk controls.
- Statistical market making/liquidity provision (only where market/data/execution conditions support it).
- Event-driven, merger/demerger and index-rebalance strategies.
- Managed futures/CTA trend following.
- Gold-silver ratio strategies.
- Cross-market/cross-venue arbitrage only where legally and technically executable.

## AI / Next-Gen
- ML models for regime/volatility/signal research (LSTM, tree models etc.).
- Deep Q-learning/RL agents, initially research/backtest/paper only with strict validation.
- NLP news/sentiment pipeline using permitted/licensed sources.
- Alternative-data alpha research (satellite/port/non-financial datasets when legally licensed).
- Multi-agent research/consensus layer.
- Quantum-inspired/Monte-Carlo/Markowitz portfolio optimization research.
- GenAI assistant for natural-language dashboard/query/control; real-money actions require explicit safeguards/confirmation.
- Optional biometric/emotional-risk circuit breaker only with explicit consent, privacy controls and appropriate device support.

## Resilience & Deployment
- Primary/secondary server failover.
- Hot standby and state recovery.
- Distributed consensus for multi-node deployment when scale justifies it.
- Observability: latency, jitter, data freshness, dropped ticks, order lifecycle, P&L and audit logs.
- Free-first deployment, then paid VPS/cloud/low-latency infrastructure migration.

## Dashboard
- Live market overview, scanner results, charts and indicators.
- Strategy status, paper/live comparison, positions, orders, fills and P&L.
- Risk/margin/kill-switch status.
- Backtest reports: CAGR/return, drawdown, Sharpe/Sortino, win rate, expectancy, costs, slippage and robustness.
- Macro + institutional metrics panels.
- Mobile-first responsive UI.

## Compliance & Safety
- Broker API and exchange/broker rules must be respected.
- SEBI/exchange/broker requirements must be checked before commercial/live automation.
- No claim of guaranteed profit, risk-free execution or guaranteed 10 ms end-to-end latency.
- Every live-order path must remain auditable and fail-safe.

## Development Rule
**One step → implement → test → fresh CI → verify PASS → update roadmap/checkpoint → STOP.**
Do not advance automatically. Continue only when the user says to proceed.
