# Cash-Future Backtest — Consolidated Status

This file is the single checklist for the Cash-Future historical backtest milestone.
GitHub `main` is the source of truth.

## Completed / implemented
- [x] Historical Cash-Future acquisition foundation.
- [x] Durable historical storage and coverage catalog.
- [x] Incremental download queue and targeted gap repair foundation.
- [x] Coverage audit/manifest and contract preflight.
- [x] Historical contract identity and lot-size handling.
- [x] Current/near contract selection foundation.
- [x] Continuous futures rollover chain.
- [x] NSE session boundary handling: 09:15–15:30.
- [x] Friday → Monday transition without weekend requests.
- [x] Generic event-driven backtest engine integration foundation.
- [x] Chunked/resumable processing and durable result/checkpoint infrastructure.
- [x] Cash-Future convergence backtest with per-contract isolation.
- [x] Multi-contract streaming backtest without mixing expiry series.
- [x] Convergence / expiry / max-holding exits.
- [x] Gap, charges and funding-cost P&L model.
- [x] Optional realistic bid/ask execution model: entry cash ASK + future BID; reverse exit cash BID + future ASK.
- [x] Missing executable prices are rejected in strict bid/ask mode; no fabricated fills.
- [x] Open positions are reported instead of fabricating an exit.
- [x] Date Results ranking: `(High - Open) × historical lot`.
- [x] Monthly Results search and monthly OHLC graph foundation.
- [x] Selected-date prior-history comparison for larger historical gaps.
- [x] Intraday 1-minute source replay with 15-minute chart / 1-minute stepping foundation.
- [x] Android Results wiring for selected-date gap ranking, prior larger gaps and replay.

## Remaining to finish Cash-Future milestone
- [ ] Connect the production Cash-Future historical downloader end-to-end to the backtest runner with verified coverage manifests.
- [ ] Make date/month Results use the same canonical Cash-Future contract/lot identity path as the backtest engine.
- [ ] Complete Futures Results UI: instrument selector + contract-month selector + future graph/replay.
- [ ] Session-aligned 15-minute replay buckets starting at 09:15.
- [ ] Portfolio-level ₹1 crore capital ledger across symbols/contracts, with capital locking and release.
- [ ] Mark-to-market equity/unrealized P&L and cumulative equity curve during an open position.
- [ ] Margin/capital checks, blocked entries and liquidation/forced-exit validation for historical Cash-Future runs.
- [ ] Realistic depth/liquidity limits and partial-fill handling for Cash-Future historical bid/ask data where depth exists.
- [ ] Rollover boundary position handling with explicit carry/close/reopen policy and no contract mixing.
- [ ] Durable full-F&O Cash-Future job integration: progress, cancel, resume, idempotent retry and bounded resource usage.
- [ ] Large-history performance validation (6 months / 1 year where data exists) without whole-history RAM materialization.
- [ ] Data-quality gate: duplicate timestamps, missing sessions, stale/crossed quotes, impossible OHLC and incomplete contract coverage.
- [ ] Real-data end-to-end reconciliation: source bars → signals → fills → costs → P&L → Results.
- [ ] Backend targeted tests + Android compile/tests + fresh GitHub CI verification.

## Locked rules
1. Shorting gap for Results = `High - Open`.
2. Ranking value = `(High - Open) × historical lot size`.
3. Historical lot size is point-in-time; never use today's lot for old dates.
4. No weekend/session-outside requests for NSE Cash-Future history.
5. No expiry/contract mixing.
6. No fabricated market data, fills or exits.
7. Bid/ask mode uses executable sides: BUY at ASK, SELL at BID.
8. If genuine finer-resolution data is unavailable, do not manufacture it from minute candles.
9. Full-year/F&O results remain incrementally persisted; never require the whole ledger in Android RAM.
10. Real/live broker order execution remains OFF for this milestone; only historical backtest and paper-trading paths are in scope.

## Completion gate
Cash-Future is considered **complete** only after every remaining checkbox is either implemented and tested or explicitly documented as unavailable because genuine historical data does not exist. Final gate: **Code → Compile → Targeted tests → Full tests → Fresh CI → PASS**.
