# VPT-X REVERSAL SCANNER — Unified Advanced Framework (LOCKED)

Authoritative page design. Future VPT-X requests must inspect this specification and the page first; do not confuse it with separate impulse/reversal strategies.

## Architecture / scope
Three independent pattern-entry engines share hidden-intelligence features. Shared data is not a merged entry decision. BUY and SELL mirrors apply. Scanner-only: no broker/order routing, paper execution, live subscriptions, runner enablement or deployment in this scope. Synthetic samples are test-only, never live signals. Thresholds are hypotheses until replay calibration; do not claim accuracy.

## Shared hidden layers
1. **Liquidity Vacuum:** forward depth at distance bands, depth slope/imbalance, cancellations vs executions, removed vs traded liquidity, sweep velocity, refill, spread and crossable distance. Cancelled orders are not executions; vacuum alone never triggers.
2. **Order-Book Resiliency:** depth consumed, replenishment latency/refill ratio, spread normalization, price-impact recovery, liquidity provider return/withdrawal. Separate from absorption.
3. **Buyer/Seller Persistence:** burst count/spacing, aggressive volume, per-burst price response/efficiency, pullbacks, persistence decay. Buyer persistence is valid context only when selling impact weakens; mirror for sellers.
4. **Flow Toxicity/Trap:** bucket imbalance/concentration, spread widening, liquidity withdrawal, impact. Continuing toxic sell impact rejects bullish reversal; continuing toxic buy impact rejects bearish. Volume alone is not signal.
5. **Positioning/OI Transition:** price-OI relation, OI change/velocity and slowing near extreme, futures basis, ATM CE/PE premium, IV/skew velocity, synchronization. OI alone is not directional.
6. **Cross-Market Lead/Lag:** synchronized spot, futures, synthetic future (ATM strike + ATM CE − ATM PE), options premium/IV, breadth, sector and heavyweight leadership; lag/divergence. Stale/missing inputs fail closed or reduce confidence, never fabricate.
7. **Control Transfer:** old-direction force decay, opposing flow acceleration/persistence, failed acceptance beyond extreme, internal pivot transition, microprice/book pressure and leadership shift. Evidence-state summary, not standalone magic score.

## Engine A — Exhaustion / Swing Reversal
Trend extends into swing extreme/sweep and continuation loses force. Measure normalized extension speed/acceleration, sweep depth vs volatility/liquidity, directional impact decay across comparable windows, absorption/refill quality, opposing-flow onset/acceleration/persistence, failed acceptance outside swing, microprice/futures divergence and failed second attack.
- BUY: low sweep → sell-impact decay → persistent opposing buyer footprint → second downside attempt fails → internal micro-high break/displacement.
- SELL: high sweep → buy-impact decay → persistent opposing seller footprint → second upside attempt fails → internal micro-low break/displacement.
Extreme or impact decay alone cannot trigger.

## Engine B — Compression / Range Reversal
Preceding impulse → consolidation/range → range-edge reversal. Measure range bounds/duration normalized by volatility, flow transition, edge impact decay/repeated attack weakening, passive refill/absorption, micro swing transition, opposite-liquidity thinning, failed breakout and acceptance back inside.
- BUY: range-low attack/false breakdown → absorption/refill → continuation below fails → buyer control shift → internal micro-high break + displacement.
- SELL: range-high attack/false breakout → absorption/refill → continuation above fails → seller control shift → internal micro-low break + displacement.
Require prior impulse and valid range; chop alone is not compression.

## Engine C — Impulse / V-Reversal
Sharp one-way impulse turns immediately, no long consolidation. Measure impulse/terminal acceleration peak, velocity collapse, impact decay, volume burst vs follow-through distance, opposing market-order arrival, quote/microprice flip, futures lead/spot lag, nearest micro-pivot reclaim/break latency.
- BUY: sell impulse peak → downside impact/follow-through collapse → aggressive buy/pressure flip → micro-pivot reclaim.
- SELL: buy impulse peak → upside impact/follow-through collapse → aggressive sell/pressure flip → micro-pivot break.
Sharp impulse alone is not V-reversal.

## Acceptance / failed auction
Bullish: time/volume below swept low, reclaim latency, time held back inside, second attempt failure, opposing displacement. Bearish mirror above swept high. Distinguish wick from acceptance; sweep is context, not trigger.

## Entry stages
- PRE-ARM: candidate zone + extreme pressure + preliminary exhaustion (watch only).
- EARLY ENTRY: control shift + opposing footprint/persistence + failed continuation + engine-specific micro-pivot + valid invalidation. Target stage.
- EXPANSION: move already underway. If early entry missed, mark MISSED; don't chase by default (late-entry rules require separate approval).

## Hard reject gates
No opposing footprint/control shift = reject. Old direction still has strong impact/continuation = reject. Trigger fails toward extreme or structural invalidation breaks = invalidate. Stale/discontinuous feed, abnormal spread, weak execution liquidity, unsynchronized required cross-market inputs, or unclear invalidation = WAIT/REJECT. Score cannot override; volume/OI/sweep/one imbalance burst never stand alone.

## Measurable feature contract
Each feature must publish formula, clock/window, rolling baseline/normalization, units, data dependencies, freshness, threshold, missing-data policy, and directional mirror. Tune thresholds/windows by replay per instrument/session; don't invent universal values or claim accuracy.
Inventory: sweep depth vs ATR/range and acceptance time/volume; flow imbalance/burst persistence; impact per aggressive volume and decay; refill ratio/latency; cancellation/execution split; spread/forward-depth/vacuum velocity; microprice/pivot latency; OI velocity/basis/synthetic divergence; cross-market lag/breadth/leaders; trigger latency, invalidation distance, MFE/MAE, slippage.

## UI contract
Display 7 shared layers; 3 independent engine cards with mirrored triggers/evidence/invalidation; PRE-ARM/EARLY ENTRY/EXPANSION; hard reject gates and feed freshness; engine-specific score/evidence; mobile compact and 4G efficient. Scanner-only status stays truthful.

## Continue rule
When asked to implement/continue/audit this scanner, read this spec and page first. Keep common hidden intelligence shared, entry engines independent, and avoid unrelated strategy/scanner/feed/deploy scope.
