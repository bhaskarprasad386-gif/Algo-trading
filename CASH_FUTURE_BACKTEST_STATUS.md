# Cash-Future Backtest — Consolidated Status

GitHub `main` is the single source of truth for the Cash-Future milestone. This file consolidates **all newly added work + all earlier remaining work** and locks the order below. Every future `Next` must start by checking this file and the current repository state; do not restart, duplicate, or skip work based on chat memory alone.

## Locked project scope
- Current implementation focus: **Cash-Future historical backtesting + live scanner + live paper trading**.
- Historical strategy application is **Cash-Future only for now**.
- Real/live broker execution is OFF and must not be added to this milestone.
- Arbitrage implementation is OFF for now.
- Existing working features must be preserved; additions should be incremental.
- Data must be durable/incremental; never require the whole history in RAM/mobile.

## Master priority order — execute from top to bottom

### P0 — Historical Cash-Future strategy application + execution core
- [ ] Select one historical date or date range.
- [ ] Select a Cash-Future strategy + configuration.
- [ ] Load only the requested historical Cash + Future observations/contracts.
- [ ] Evaluate strategy strictly point-in-time; no look-ahead.
- [ ] Normalize strategy BUY/SELL signals into historical Cash-Future actions.
- [ ] Preserve existing convergence strategy and APIs; do not replace them.
- [ ] Execute signals through the existing Cash-Future executable-price model.
- [ ] Apply point-in-time historical lot size, charges, funding, slippage/bid-ask and expiry rules.
- [ ] Persist strategy-run metadata, signals, trades, P&L and equity for comparison/replay.
- [ ] Add Cash-Future strategy-run API.
- [ ] Add targeted tests: date filtering, BUY/SELL, no-look-ahead, deterministic replay and contract isolation.

### P1 — ₹1 crore portfolio capital + realistic risk/execution
- [ ] Default historical/backtest portfolio capital = **₹1,00,00,000**.
- [ ] Portfolio-level capital ledger across simultaneous symbols/contracts.
- [ ] Lock required capital/margin on entry and release it on exit.
- [ ] Prevent over-allocation and record blocked-entry reason/count.
- [ ] Mark-to-market unrealized P&L for open positions.
- [ ] Cumulative realized + unrealized equity curve.
- [ ] Margin checks and historical forced liquidation/exit validation.
- [ ] Realistic depth/liquidity limits and partial fills only where genuine historical bid/ask/depth exists.
- [ ] Explicit rollover-boundary position policy: close/reopen or carry; never mix expiry-series prices.

### P2 — Production historical data pipeline
- [ ] Connect production Cash-Future historical downloader end-to-end to the backtest runner.
- [ ] Require verified coverage manifests before a historical run is considered complete.
- [ ] Durable full-F&O Cash-Future jobs: progress, cancel, resume, idempotent retry, bounded resources.
- [ ] Validate 6-month and 1-year runs where genuine data exists, without whole-history RAM materialization.
- [ ] Data-quality gates: duplicate timestamps, missing sessions, stale/crossed quotes, impossible OHLC, incomplete contracts.
- [ ] Source → bars → strategy signals → fills → costs → P&L → Results reconciliation.
- [ ] Preserve genuine finer-resolution data when available; never manufacture millisecond data from minute candles.

### P3 — Results / Calendar / Replay completion
- [ ] Make date/month Results use the same canonical Cash-Future contract identity + point-in-time lot path as the backtest engine.
- [ ] Switch Android date ranking to canonical `/date-gap` with `mode=shorting`.
- [ ] Switch monthly search to one `/monthly-gap` request instead of day-by-day looping.
- [ ] Complete Futures Results UI: instrument selector + contract-month selector + future graph/replay.
- [ ] Preserve ranking: `(High - Open) × point-in-time historical lot`.
- [x] Intraday replay uses 1-minute source data and displays 15-minute candles with 1-minute stepping.
- [x] Replay 15-minute buckets align to NSE 09:15 session start: 09:15, 09:30, …, 15:15.
- [ ] Add replay tests for 09:15/09:30/09:45 and session-end behavior.

### P4 — Scanner + paper-trading integration
- [ ] Keep Cash-Future live scanner operational independently of historical backtesting.
- [ ] Live paper trading for arbitrary strategy BUY/SELL actions.
- [ ] Paper capital default = ₹1 crore.
- [ ] Paper fills should behave like real execution using available live prices/bid-ask; no fake market data.
- [ ] Persist live paper observations/trades so they become future backtest data.
- [ ] Paper risk controls: max orders/day, max position size, max loss and kill switch.
- [ ] Keep live broker execution disabled.

### P5 — Final validation gate
- [ ] Backend compile.
- [ ] Cash-Future targeted backend tests.
- [ ] Android compile/tests for Results/replay and later Cash-Future strategy UI.
- [ ] Full test suite.
- [ ] Fresh GitHub Actions run verified PASS.
- [ ] Only after all applicable items pass: Cash-Future milestone marked complete.

## Completed / implemented
- [x] Historical Cash-Future acquisition foundation.
- [x] Durable historical storage and coverage catalog.
- [x] Incremental download queue and targeted gap-repair foundation.
- [x] Coverage audit/manifest and contract preflight.
- [x] Historical contract identity and lot-size handling.
- [x] Current/near contract selection foundation.
- [x] Continuous futures rollover chain.
- [x] NSE session boundaries 09:15–15:30.
- [x] Friday → Monday transition without weekend requests.
- [x] Generic event-driven backtest engine integration foundation.
- [x] Chunked/resumable processing and durable result/checkpoint infrastructure.
- [x] Cash-Future convergence backtest with per-contract isolation.
- [x] Multi-contract streaming backtest without expiry-series mixing.
- [x] Convergence / expiry / max-holding exits.
- [x] Gap, charges and funding-cost P&L model.
- [x] Optional realistic bid/ask execution: entry cash ASK + future BID; reverse exit cash BID + future ASK.
- [x] Missing executable prices rejected in strict bid/ask mode; no fabricated fills.
- [x] Open positions reported instead of fabricating exits.
- [x] Date Results ranking: `(High - Open) × historical lot`.
- [x] Monthly Results search and monthly OHLC graph foundation.
- [x] Selected-date prior-history comparison for larger historical gaps.
- [x] Intraday 1-minute source replay with 15-minute chart / 1-minute stepping.
- [x] Android Results wiring for selected-date gap ranking, prior larger gaps and replay.
- [x] Session-aligned 15-minute replay buckets starting at 09:15.

## Locked rules
1. Shorting gap for Results = `High - Open`.
2. Ranking = `(High - Open) × historical lot size`.
3. Historical lot size is point-in-time; never use today's lot for an old date.
4. No weekend/session-outside NSE requests.
5. No expiry/contract mixing.
6. No fabricated market data, fills or exits.
7. Bid/ask mode uses executable sides: BUY at ASK, SELL at BID.
8. No finer-resolution data may be manufactured from coarser candles.
9. Full-year/F&O results are incrementally persisted.
10. Real/live broker order execution remains OFF.
11. Historical strategy application is Cash-Future only until this milestone is validated.
12. Strategy evaluation is strictly point-in-time; no look-ahead.
13. Default historical/backtest portfolio capital = ₹1,00,00,000 unless explicitly overridden for a run.
14. Existing completed behavior must be preserved when adding new layers.
15. **Every future continuation starts by checking this status file + current GitHub state.**
16. Do not mark work complete without implementation/test evidence.

## Current checkpoint
**Next implementation = P0: Historical Cash-Future strategy application + execution core.**

The implementation must first inspect the current Cash-Future engine, historical data model, execution path, persistence layer and tests, then add the smallest verifiable chunk. Do not jump directly to UI or portfolio features before the P0 backend foundation is sound.

## Completion gate
Cash-Future is complete only when every applicable P0–P5 item is implemented and tested, or explicitly documented as unavailable because genuine historical data does not exist.

Final gate: **Code → Compile → Targeted tests → Full tests → Fresh CI → PASS**.
