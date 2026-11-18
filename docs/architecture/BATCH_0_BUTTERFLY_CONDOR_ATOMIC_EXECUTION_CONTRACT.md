# Batch 0 — Box Spread / Butterfly / Condor Multi-Leg Execution Safety Contract

**Status:** Required design and acceptance contract; not live-routing authorization.  
**Parent:** `batch0/cumulative-overfill-rollback-guard` (PR #631).  
**Scope:** Box Spread, Credit Butterfly and Credit Condor alerts using one shared account-scoped all-legs-or-recover execution lifecycle.  
**Non-goals:** No scanner/WebSocket subscription changes, no backtesting, no cash-future/calendar/synthetic strategy work, no live-order enablement in this change.

## 1. Review findings from the current implementation

The repository already contains a useful shadow scanner at `backend/app/scanner/butterfly_condor_shadow.py` and a small multi-leg watchdog at `backend/app/execution/multileg.py`.

The shadow scanner:
- Evaluates Butterfly and Condor candidates from the shared quote/depth snapshot and explicitly does not subscribe to feeds or route orders.
- Uses executable-side prices (BUY at ask, SELL at bid), depth VWAP, common-lot capacity, quote/depth freshness, and cross-leg timestamp skew checks.
- Represents a Butterfly as three strike legs with a 2x middle-leg ratio; a Condor is four legs.
- Exposes depth readiness, but this is still a scanner/shadow result, not a live execution authorization.

The current multi-leg watchdog only returns a WAIT/ROLLBACK/SQUARE_OFF decision. It does not itself cancel broker orders, reconcile uncertain acknowledgements, or close broker-side fills. The generic broker adapter contract also does not establish a portable atomic basket-order API.

**Conclusion:** These strategies are currently shadow/scanner calculations, not proven live multi-leg execution strategies. Do not connect their alerts directly to a broker adapter until the acceptance gates below are implemented and tested.

## 2. Hard execution invariant

A strategy intent is successful only when every required leg reaches its required filled quantity in the intended ratio, all fills are persisted and reconciled, and the final basket still satisfies its approved risk/price limits.

- Box Spread: 4 distinct option legs; the strategy definition declares the exact CE/PE strike pairing and side ratios.
- Butterfly: 3 distinct option legs; the body leg has a 2x quantity multiplier relative to each wing.
- Condor: 4 distinct option legs; all four legs use the defined equal lot ratio unless a strategy contract explicitly declares another ratio.
- The coordinator must derive the expected leg count and ratio from the immutable strategy definition. It must not infer a 3-leg/4-leg basket from a client-supplied count.
- A partial basket is never reported as FILLED, COMPLETED, or a successful arbitrage.
- A broker API accepting an order is not a fill. Acknowledgement, open quantity, partial fill, and final trade fill remain distinct states.

True exchange-level atomicity across independent option contracts cannot be promised unless a broker/exchange provides a supported native basket/complex-order facility with documented atomic semantics. The default protocol is therefore application-level coordinated execution with explicit residual-exposure handling—not a false guarantee of simultaneous exchange fills.

## 3. Alert-time executable-price gate

An alert is a candidate, not an order instruction. At alert receipt, the execution service must create an immutable, account-scoped intent containing:
- strategy family and exact strategy variant;
- underlying, expiry, option type, strikes, instrument tokens and lot size;
- ordered leg definitions, BUY/SELL side, ratio multiplier and target quantity;
- scanner alert ID, alert timestamp, quote/depth snapshot identity and per-leg reference prices;
- account/broker identity, execution mode, idempotency key and risk-policy version.

Before any broker side effect, fetch/read one coherent latest market snapshot and validate every leg together:

1. All instruments resolve to the same intended underlying/expiry and valid contract master entries.
2. All legs have valid, non-crossed bid/ask books, positive prices and sufficient executable depth for the *whole ratio-adjusted basket quantity*.
3. Quotes and depth are within configured maximum age and cross-leg timestamp skew. Missing, stale, crossed, out-of-order or incomplete depth fails closed.
4. BUY legs are priced from asks and SELL legs from bids; depth VWAP is calculated for the exact required quantities. LTP/midpoint/last alert price must never be substituted for executable prices.
5. Recompute basket credit/debit and payoff from the fresh executable prices. Recompute net edge after configured brokerage, exchange charges, STT, GST, stamp duty, slippage reserve and any other applicable costs. Do not call a positive gross credit “arbitrage” if net edge or worst-case risk is unacceptable.
6. Compare the new executable basket against the scanner alert snapshot. If the alert has aged out, any leg price moved outside its collar, the net edge fell below the configured threshold, or common depth no longer supports the requested lots, reject the entire intent. Never silently chase a vanished edge.
7. Verify margin/funds, max basket loss, max open exposure, per-account limits, market status, kill switch and broker capability before reserving risk.
8. Persist the approved price/depth snapshot and price collars. The broker order limits must be derived from those approved prices, side-aware tick rounding and the explicit slippage budget.

The same check is repeated immediately before submission. A material quote/depth change between preflight and submit invalidates the approval and requires a fresh full-basket evaluation; do not reprice one leg in isolation.

## 4. Coordinated submission and fill barrier

After all preflight checks pass and risk is durably reserved:

1. Persist the intent and deterministic per-leg client order IDs before external calls.
2. Submit every leg through a bounded, broker-capability-aware concurrent submission window. Do not submit one leg and wait indefinitely before considering the others. Do not assume concurrency makes the exchange atomic.
3. Record each broker acknowledgement separately; unknown transport outcomes become UNKNOWN and trigger reconciliation, never blind retry.
4. Consume order/trade updates and reconcile cumulative fills by account, broker, order ID, fill ID and intent. Duplicate callbacks must be idempotent.
5. Enforce ratio-matched fill progress. For Butterfly, one body lot (2 units) must match two wing lots (1 unit each) for the same basket quantity. For Condor, all four leg quantities must match their declared ratios.
6. The basket reaches FILLED only when every required leg's cumulative confirmed fill equals its target quantity and all fill records are durable. A filled wing/body by itself never counts as a completed trade.

## 5. Any-leg failure, partial fill, or timeout — emergency unwind

**Safest default:** cancel and reconcile first, then close the exact broker-confirmed residual option positions with bounded, side-aware LIMIT IOC orders. Do not automatically add a new hedge leg by default: an improvised hedge can introduce basis, gamma, volatility, margin and additional execution risk. A hedge may be used only when a strategy-specific hedge instrument, ratio, freshness gate, loss budget and exit path were approved before entry. Never use an unbounded market order or chase indefinitely.

If any leg rejects, partially fills outside the permitted synchronized ratio, times out, or becomes UNKNOWN:

- Immediately freeze the intent: stop submitting unsent legs and prevent new entry attempts for that intent.
- Cancel all confirmed working/unfilled orders using deterministic client IDs; cancellation acknowledgement alone is not proof that no late fill occurred.
- Reconcile broker order book, trade book and positions for every leg. Late fills received during cancellation are included in residual exposure.
- Calculate the exact net residual position per instrument and account from confirmed fills—not from requested quantities or local optimistic state.
- Submit idempotent emergency close/unwind orders for the confirmed residual positions using pre-approved side-aware price collars and a separately bounded emergency loss/slippage budget.
- Continue reconciliation until every residual is confirmed flat or a terminal manual-review state is reached. Do not claim the basket is rolled back merely because cancel requests were sent.
- If an unwind order is rejected, unavailable, partially filled, or its status is unknown, latch the account/strategy entry kill switch, raise a high-priority operator alert, retain the risk reservation, and require explicit recovery/manual review. Never hide an open leg.
- Persist the full event trail: alert snapshot, approved prices, submissions, acknowledgements, fills, cancels, late fills, unwind orders, realized slippage/P&L, state transitions and operator actions.

“Immediately” means trigger the cancel/reconcile/unwind workflow on the first detected failure without waiting for the normal strategy cycle. It does not mean the broker guarantees an immediate fill. Market gaps, circuit limits, liquidity gaps, exchange halts and broker outages can make an unwind impossible or expensive; the system must expose that residual risk and fail closed.

## 6. State machine additions / interpretation

Use the existing durable intent lifecycle where possible. Add explicit basket-level states (or equivalent persisted reason codes) for:
- PREFLIGHT_REJECTED
- PREFLIGHT_APPROVED / RISK_RESERVED
- SUBMITTING_ALL_LEGS
- WORKING_ALL_LEGS
- PARTIALLY_FILLED_UNHEDGED
- CANCEL_REQUESTED
- RECONCILIATION_REQUIRED
- EMERGENCY_UNWIND
- FLAT_AFTER_UNWIND
- MANUAL_REVIEW_RESIDUAL_EXPOSURE
- FILLED / COMPLETED

State changes must be transactional/compare-and-set and append audit events. Do not overload CANCELLED to mean flat: a cancelled order can still have fills and residual positions. Do not mark ROLLBACK complete until broker-side positions prove the residual exposure is flat.

## 7. Strategy-specific payoff and sizing checks

- Validate exact strike ordering and equal strike spacing required by the selected scanner structure.
- Butterfly: validate three legs, outer-wing direction and 2x body ratio; reject any mismatched lot size or contract multiplier.
- Condor: validate four legs and the exact inner/outer side pattern for the selected LONG/SHORT variant; validate equal spacing and lot/contract consistency.
- Validate expiry and option type on every leg; do not mix weekly/monthly expiries or CE/PE legs accidentally.
- Recalculate expiry payoff and maximum loss from the exact leg plan and quantities. Credit received is not the same as guaranteed profit; fees, execution slippage and expiry payoff remain material.
- Apply broker freeze quantity, lot size, tick size, price band, order-type and rate-limit capabilities to each leg.
- Basket quantity is limited by the minimum ratio-adjusted executable depth across all legs and account risk/margin, not by the most liquid leg.

## 8. Required failure-injection acceptance matrix

| Scenario | Required outcome |
|---|---|
| Alert price is stale or one leg moved beyond collar | Reject before any broker order |
| One leg has missing/stale/crossed depth | Reject whole basket |
| Net edge becomes non-positive after fees/slippage | Reject whole basket |
| Insufficient depth for one ratio-adjusted leg | Reduce whole-basket lots only if explicitly allowed; otherwise reject |
| One submission rejected before any fill | Cancel remaining working orders; reconcile all legs |
| One leg fills and another rejects | Freeze, cancel, reconcile and unwind filled residual immediately |
| Butterfly body fills 2x while one wing is missing | Treat as unhedged residual; emergency unwind |
| One leg partially fills | No basket success; cancel remainder and unwind/reconcile residual according to policy |
| Duplicate fill callback | No duplicate quantity, position, or unwind |
| Late fill after cancel acknowledgement | Reopen residual calculation and unwind; never assume flat |
| Broker API timeout after submit | UNKNOWN + reconcile by client order ID; never blind resubmit |
| Process restart during submit/unwind | Restore from durable intent/events and reconcile before any retry |
| Unwind rejected/unknown/partial | Kill new entries, retain risk, alert operator, MANUAL_REVIEW |
| Kill switch during multi-leg submission | Stop unsent legs, cancel working orders, reconcile and unwind confirmed fills |
| Same alert delivered twice | One idempotent intent; no duplicate basket |
| Broker/account identity changes mid-flight | Reject mutation and isolate reconciliation by original account/broker |

Every scenario must be covered in pure unit tests and broker-adapter contract/integration tests with deterministic fake adapters. Paper must exercise the same coordinator and state machine, but must never call a live broker adapter.

## 9. Batch 0 delivery sequence

1. Keep the current shadow scanner read-only and preserve its shared-snapshot/no-new-subscription behavior.
2. Add immutable strategy-leg contracts and alert-to-intent mapping for Butterfly and Condor.
3. Implement a shared basket preflight gate (freshness, depth, ratio-adjusted capacity, executable price, fees, payoff, margin and risk).
4. Implement durable coordinated submission, per-leg fill barrier, idempotent cancel and reconciliation.
5. Implement bounded emergency unwind with residual-position verification and kill-switch escalation.
6. Add restart/late-fill/duplicate-event recovery and broker capability conformance.
7. Prove the complete workflow in Paper first. Keep Live routing disabled until all broker-specific acceptance gates and operational recovery tests pass.

This shared invariant applies equally to Box Spread, Credit Butterfly and Condor. It must not alter Box Spread scanner subscriptions, the existing server WebSocket, or unrelated runners.
