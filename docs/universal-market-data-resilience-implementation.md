# Shared WebSocket and Runner — Current Architecture

**Verified baseline:** `main` at `57abed5f559ae7a6410a4c0165ec818a4e07b8f5` (28 Nov 2026).
This document describes the current production code paths. Older batch notes and implementation proposals in prior revisions are historical records, not current runtime claims.

## 1. Production ownership

- `SharedIndexMarketDataRunner` is the application-owned NIFTY/BANKNIFTY market-data runner.
- Its `SharedIndexFeedLifecycle` owns exactly three consumer registrations: `spot`, `options`, and `futures`.
- Those registrations use the process-wide `SharedIndexOnlyWebSocketManager`, backed by `CommonWebSocketManager`.
- The production manager accepts only the three `shared-index-market-data:{spot,options,futures}` consumers and validates the locked NSE/NFO NIFTY/BANKNIFTY instrument universe before registry mutation.
- IV Skew Flip attaches read-only adapters to the runner's published state. It does not own broker subscriptions, a broker socket, an instrument-master download, or runner shutdown.
- The application lifespan starts the Shared Runner recovery loop and the persisted IV Scanner recovery loop. IV OFF detaches IV readers; it does not stop the Shared Runner.
- No Box Spread, Cash-Future, Calendar Spread, or Synthetic market-data runner is part of this locked production startup topology.

## 2. Startup lifecycle isolation

- Instrument-master download remains on the fetch-only daemon worker; that helper must never create sockets, mutate runner state, or start/stop runtimes.
- Shared Runner start/stop calls are serialized by the dedicated `SerializedLifecycleWorker`, not executed on the ASGI event loop.
- Lifecycle commands are FIFO. Cancelling the recovery coroutine does not abandon a broker SDK call already in progress; shutdown queues runner OFF behind that operation and awaits the stop barrier.
- Admission closes atomically with enqueue during worker shutdown, so a new ON cannot be queued behind the terminal stop. If the worker misses its bounded join deadline, the lifecycle fails closed rather than admitting another generation.
- This worker owns only Shared Runner lifecycle operations. Market-data callbacks, instrument refresh, option rotation, and IV reader lifecycle remain on their existing dedicated owners.

## 3. Locked physical socket lanes

| Physical group | Allowed instruments |
|---|---|
| `(mode=3, shard=0)` | NIFTY spot and NIFTY WEEKLY options |
| `(mode=3, shard=1)` | BANKNIFTY spot and BANKNIFTY MONTHLY options |
| `(mode=3, shard=2)` | NIFTY and BANKNIFTY index futures |

The global manager cap is three physical sessions and 1,000 tokens per session. Lane placement is descriptor-based, not consumer-name-based. A fourth physical group or conflicting instrument identity is rejected before live assignment/route mutation. Recovery recreates a failed lane in its existing `(mode, shard)`; no cross-lane redistribution is allowed.

## 4. Callback and control-plane separation

The Angel transport callback enters the common manager's bounded per-shard callback adapter. The adapter copies and queues normalized delivery work with generation fencing and fail-closed queue-overflow handling.

The Shared Runner's market-data callback now performs only:
1. runner-generation/running check;
2. canonical state acceptance and cache publication;
3. update of the latest NIFTY or BANKNIFTY spot record;
4. non-blocking submission to a two-key latest-value mailbox.

Instrument-master refresh admission, option bootstrap/selection, ATM rotation, expiry rollover, and futures rollover run on the dedicated `shared-index-control` worker. The mailbox stores at most one newest pending record per locked underlying; intermediate spot updates are coalesced. It cannot grow with tick rate.

The control worker is generation-fenced across ON/OFF. OFF invalidates the active runner generation, tears down subscriptions under the runner lifecycle lock, then joins the worker outside that lock. A stopped-event barrier prevents a subsequent ON from overtaking that join; if the worker misses its shutdown deadline, the runner fails closed and refuses to start a new worker until the old thread is confirmed stopped. Health/snapshot diagnostics expose mailbox capacity, pending symbols, coalesced updates, processed batches/records, failures, and last/max control-batch duration.

Instrument-master refresh remains separately bounded and generation-fenced. It publishes only a complete validated master snapshot and preserves the last good snapshot on failure.

## 5. Readiness and quote freshness contract

These signals are deliberately distinct:

- `core_market_data_ready`: both NIFTY/BANKNIFTY spot and futures records are fresh.
- `live_data_ready`: core readiness, complete subscription/observed universe, bootstrap complete, and the established minimum activity policy (at least 50% fresh on each CE/PE side for each underlying).
- `option_quote_coverage.complete`: every expected option record is fresh; this is a stricter coverage metric, not implied by `live_data_ready`.
- `all_records_fresh`: the full 128-instrument Shared Runner record set is present and fresh.

IV applies a stricter per-selected-contract rule:
- Every accepted spot/option record must have a positive broker `exchange_timestamp_ns` equal to the canonical `timestamp_ns`. A local timestamp fallback is not exchange evidence and is rejected.
- Spot and selected option quotes must be no older than five seconds (with a one-second future-clock tolerance).
- The option adapter reports expected, fresh, missing, and two-sided quote counts by exact token/expiry/strike/side identity.
- IV skew/flip evaluation is withheld unless every contract in that selected IV expiry window has a fresh broker-timestamped quote. Partial coverage is reported as WAITING, never as a directional signal.

Transport OPEN, a sent subscription, or retained last-known data alone never proves fresh market data. Angel One's SDK does not expose an independently verifiable authentication acknowledgement; diagnostics must continue to report authentication as not verifiable rather than infer it from transport state.

## 6. Legacy code and documentation retirement

The earlier document versions referred to `backend/app/market_data/live_cash_future_stream.py` and `backend/app/market_data/engine.py` as remaining direct-socket compatibility paths. Neither path exists in the verified repository tree at the baseline above. Those references are retired and must not be used as current runtime evidence.

The old statements that Box Spread, Cash-Future, Calendar Spread, and Synthetic are feature-flagged active consumers of this production three-lane runner are also obsolete. The production owner guard rejects their former consumer IDs. Historical PR/batch notes may remain in Git history, but current operational documentation must use this file's ownership model.

Do not reintroduce a strategy-owned `MarketDataWebSocket`, direct `SmartWebSocketV2`, or independent Angel instrument-master fetch in IV or any retired strategy. New consumers require an explicit, reviewed change to the locked owner contract.

## 7. Verification required for a release

- Unit tests for the two-key coalescing mailbox, generation rejection, bounded pending state, and shutdown behavior.
- Runner tests proving spot callback dispatches control work asynchronously while preserving bootstrap/rotation/rollover correctness.
- IV tests for missing provider timestamps, stale/missing selected tokens, wrong expiry/strike/side identity, and signal suppression on partial coverage.
- Full backend, targeted common-feed, and integration CI on the exact PR head.
- After deployment is separately authorized: inspect runtime health, worker diagnostics, lane roster, queue overflow, reconnects, stale quote counts, and p50/p95/p99 callback-to-consumer latency during a representative market-hours soak.

No VPS restart, deployment, trading enablement, or strategy activation is authorized by this code change alone.


## 8. Angel One broker-native IV signal path

- The existing `MarketDataClient.option_greeks()` client is reused. No broker session, WebSocket, subscription, or second market-data connection is created.
- Production IV runtime injects one `AngelOneBrokerIVCache` worker. It requests Greeks for the active NIFTY Weekly and BANKNIFTY Monthly expiry contexts on a bounded 30-second per-chain cadence (at most two requests per 30 seconds, sequentially); the existing client retry policy remains bounded.
- REST work runs only on the dedicated `iv-angelone-greeks-rest` worker. Shared WebSocket callbacks do not call REST or wait for Greeks.
- Cache identity is exact underlying + expiry + strike + CE/PE + resolved instrument token. The broker-returned expiry is mandatory; duplicate rows, missing/invalid IV, wrong expiry and incomplete chain coverage are rejected.
- Angel One Greeks has no exchange event timestamp. Its timestamp is explicitly the local response-capture time, never represented as an exchange tick time. Live use is limited to 90 seconds after capture.
- `OptionQuote` carries broker IV, `ANGEL_ONE` provenance and capture timestamp separately from the WebSocket quote timestamp. The signal engine accepts only finite, positive broker IV with fresh capture provenance; local IV inversion remains disabled.
- Missing, stale, partial, mismatched, or unavailable broker IV makes both IV Skew and IV Flip WAITING and prevents the confluence signal. LAST snapshot/history rows are not read by the live signal path.
- Expiry rotation and worker shutdown fence late REST responses so an old response cannot repopulate a retired cache generation.
- Diagnostics expose worker state, cadence, capture status, per-chain expected/fresh/missing counts and confirm zero REST calls from WebSocket callbacks.
- Release remains gated on the complete targeted and backend CI suite. No deployment, restart, or trading activation is implied by this change.
