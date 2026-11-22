# Short Covering & Long Unwinding — Phase 1 contract

Goal: detect 30s–3m pre-impulse pressure for NIFTY/BANKNIFTY. Independent bullish short-covering and bearish long-unwinding evidence, never one score inverted.

**Guardrails:** scanner-only, no order routing/paper execution. No changes to Box Spread, IV Scanner, shared websocket/feed ownership or other runners. Only timestamped real observations. Unavailable aggressor/open-close intent/dealer gamma are unknown, not fabricated. Stale, unsynchronized, conflicting, low coverage or poor execution-quality data fail closed.

**Evidence groups:** Liquidity (sweep/reclaim), Order Flow (absorption/impact/imbalance), Futures (lead-lag/basis/OI), Options (ATM± strikes IV/OI/volume/migration), Structure (VWAP/AVWAP/trigger/invalidation), Participation (cross-index with correlation-aware weighting). Current evaluator accepts normalized group evidence [-1,+1] and emits separate scores, conflict, coverage and gates. This is a scoring scaffold—not calibrated probability nor a live pipeline.

**State lifecycle:** IDLE → PRECONDITION → PRESSURE_BUILDING → PRE_IMPULSE → TRIGGERED → ACTIVE; terminal CONFLICT/INVALIDATED/EXPIRED. Event timestamps, feed epoch and bounded TTL required.

**Phases:** 1 page/scoring scaffold/tests; 2 feed feasibility & synchronized features/adaptive MAD; 3 liquidity map/sweep/trap memory; 4 absorption/impact/vacuum only from supported depth/executed flow; 5 futures lead/basis acceleration/cross-index; 6 options OI/IV velocity; 7 event sequence/state machine; 8 replay/walk-forward/cost/latency/slippage/calibration. Never call scores probabilities or claim accuracy until out-of-sample calibration.
