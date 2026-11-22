# VPT-X NIFTY / BANKNIFTY Reversal Research Engine (Phase 1)

## Scope and safety
- Underlyings: NIFTY and BANKNIFTY only.
- Scanner/evaluator only. No broker API, Angel One, Shoonya, websocket subscription, order routing, paper fills, live enable switch, or feed lifecycle is included.
- Accepts normalized timestamped observations; future feed wiring requires explicit approval.
- Signals are candidates, not probabilities or trading recommendations.

## Pattern engines
1. Swing Exhaustion: liquidity extreme/sweep or reclaim, directional impact decay, opposite flow or repeated-attack failure.
2. Compression Reversal: opposing flow transition, micro-pivot, repeated attack failure.
3. V-Reversal: impact decay, opposite-flow transition, micro-pivot/displacement.

Evidence families:
- liquidity failure (25)
- directional force decay (20)
- opposite flow transition (20)
- repeated attack failure (15)
- microstructure pressure (10)
- early displacement (10)

Cross-market confirmation is an optional, small bonus; future feed wiring must use synchronized evidence.

## Input schema
underlying: NIFTY or BANKNIFTY
ticks: timestamp_ms, price, optional buy_volume, sell_volume, bid_qty, ask_qty, bid, ask
cross_confirmation: optional normalized value from a synchronized cross-market evaluator.
Absent fields are neutral. No fabricated data.

## Data gates
- Minimum 24 valid ticks
- Max tick age 5 seconds
- Max 500 tick observations per evaluation
- Invalid prices/timestamps dropped; duplicate timestamps keep latest
- Unsupported underlying and stale/insufficient ticks fail closed

## Output
Six directional candidates (3 patterns x BUY/SELL) with score, evidence families, trigger readiness, invalidation extreme, target reference, scanner-only and no-order-routing flags.

## Later phases (not authorized)
- Angel One / Shoonya feed subscriptions or websocket integration.
- Market-specific threshold calibration from replay data.
- Paper tracking, slippage/latency evaluation.
- UI/API exposure and alerting.
