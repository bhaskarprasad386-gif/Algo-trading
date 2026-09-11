# Cash-Future Backtest — Consolidated Status

GitHub `main` is the source of truth. This checklist consolidates the newly added Results/replay work with the earlier Cash-Future backlog and locks the implementation order by the current project preference.

## Priority order — work from here

### P0 — Historical Cash-Future strategy application + execution core
- [ ] Historical/back-date Cash-Future strategy runner: select one date or date range and one strategy/config; apply it only to historical Cash + Future observations.
- [ ] No-look-ahead signal evaluation: strategy receives only data available at the current historical timestamp.
- [ ] Normalize strategy BUY/SELL signals into Cash-Future historical actions without enabling live broker orders.
- [ ] Route strategy trades through the same Cash-Future executable-price model, lot-size, charges, funding and expiry rules.
- [ ] Persist strategy-run metadata, signals, trades, P&L and equity so runs can be compared/replayed later.
- [ ] Add Cash-Future strategy-run API and tests for date filtering, BUY/SELL, no-look-ahead and deterministic replay.

### P1 — ₹1 crore portfolio capital + risk realism
- [ ] Portfolio-level default paper/backtest capital: ₹1,00,00,000 across symbols/contracts.
- [ ] Capital/margin lock on entry and release on exit; prevent over-allocation across simultaneous positions.
- [ ] Mark-to-market unrealized P&L and cumulative equity curve while positions remain open.
- [ ] Margin/capital checks, blocked-entry reason/count and historical forced liquidation/exit validation.
- [ ] Realistic depth/liquidity and partial fills only where genuine historical bid/ask/depth data exists.
- [ ] Explicit rollover-boundary position policy: close/reopen or carry, never mix expiry-series prices.

### P2 — Historical data → backtest production pipeline
- [ ] Connect production Cash-Future historical downloader end-to-end to the backtest runner with verified coverage manifests.
- [ ] Durable full-F&O Cash-Future job: progress, cancel, resume, idempotent retry and bounded resources.
- [ ] Large-history validation: 6 months / 1 year where data exists, without whole-history RAM materialization.
- [ ] Data-quality gate: duplicate timestamps, missing sessions, stale/crossed quotes, impossible OHLC and incomplete contract coverage.
- [ ] Real-data reconciliation: source bars → signals → fills → costs → P&L → Results.

### P3 — Results / Calendar / Replay completion
- [ ] Make date/month Results use the same canonical Cash-Future contract/lot identity path as the backtest engine.
- [ ] Switch Android date ranking to canonical `/date-gap` shorting mode and monthly search to one `/monthly-gap` request.
- [ ] Complete Futures Results UI: instrument selector + contract-month selector + future graph/replay.
- [ ] Keep date ranking: `(High - Open) × point-in-time historical lot`.
- [x] Intraday replay source uses 1-minute data and displays 15-minute candles with 1-minute stepping.
- [x] Replay 15-minute buckets are aligned to NSE 09:15 session start (09:15, 09:30, …, 15:15).
- [ ] Add targeted replay tests for 09:15/09:30/09:45 and session-end behavior.

### P4 — Final validation gate
- [ ] Backend targeted tests.
- [ ] Android compile/tests for Results/replay changes.
- [ ] Full test suite.
- [ ] Fresh GitHub Actions run verified PASS.
- [ ] Only after all above: Cash-Future milestone marked complete.

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
- [x] Session-aligned 15-minute replay buckets starting at 09:15.

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
11. Historical strategy application is currently Cash-Future only; do not generalize it to other backtest modes until Cash-Future is validated.
12. Strategy evaluation must be strictly point-in-time with no look-ahead.
13. Default historical/backtest portfolio capital is ₹1,00,00,000 unless a run explicitly overrides it.

## Current next implementation
Start at **P0: Cash-Future historical strategy application + execution core**. Preserve the existing convergence backtest and Results APIs; add the strategy layer around them rather than replacing working behavior.

## Completion gate
Cash-Future is considered complete only after every applicable P0–P4 checkbox is implemented and tested, or explicitly documented as unavailable because genuine historical data does not exist. Final gate: **Code → Compile → Targeted tests → Full tests → Fresh CI → PASS**.
