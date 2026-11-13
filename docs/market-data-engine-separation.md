# Market Data Engine Separation — Architecture Contract

Status: proposed implementation contract for the institutional market-data batches.

## Non-negotiable separation

The runtime has two independently supervised engine classes. They must not be collapsed into one callback path.

### Engine A — Broker Live Data Acquisition
- Own broker-specific authentication, WebSocket lifecycle, heartbeat, reconnect, subscription admission and transport health.
- Maintain independent Angel One and Shoonya adapters and independent connection budgets.
- Receive broker frames, attach broker identity, socket-generation identity and receive timestamp, then hand off an immutable raw frame envelope.
- Perform only minimum framing and safety checks needed to reject malformed frames or callbacks from obsolete socket generations.
- Never calculate indicators, IV, ATM/strike selection, Box Spread edges, signals, strategy eligibility, P&L or entry/exit decisions.
- Never block on strategy callbacks, database writes, Telegram delivery or UI updates.

### Engine B — Market Data Processing and Calculation
- Consume acquisition envelopes from bounded ingress queues.
- Validate instrument identity and timestamps; normalize broker payloads into the canonical market-data contract.
- Maintain quote snapshots and freshness by broker, exchange and instrument.
- Run market-data calculations and publish canonical records to isolated strategy consumers.
- Isolate slow consumers so one scanner cannot block another scanner or the broker acquisition loop.
- Expose queue depth, processing latency, rejected/out-of-order frames, worker liveness and per-consumer delivery failures.

## Broker isolation
- Angel One remains one shared acquisition manager for Box and IV, subject to the global three-socket budget.
- Shoonya remains an independent acquisition manager with its own authentication, socket, subscription registry, reconnect policy and 500-token cap.
- A failure, reconnect, queue overflow or shutdown in one broker must not close or restart the other broker.
- Shoonya remains default-off and shadow-only until separately approved and validated. No provider selection, scanner fanout, persistence or order routing is enabled by this document.

## Data hand-off contract
Every ingress envelope must include:
- broker
- connection_generation
- exchange
- token
- received_at_ns (local monotonic receive time, separate from source exchange time)
- source_timestamp when supplied by the broker
- immutable or copy-isolated raw_payload

Processing must reject envelopes from retired connection generations and reject strictly older source timestamps per instrument. Equal timestamps may carry legitimate quote changes and must not be rejected solely for equality.

## Backpressure and integrity
- Queues must be bounded and expose capacity and high-water telemetry.
- The acquisition callback must use non-blocking enqueue; it must never wait for calculation or database work.
- Queue overflow is an explicit data-integrity fault: mark that broker feed degraded, count the loss and trigger controlled recovery/resynchronization. Never silently drop and continue reporting LIVE.
- Shutdown uses a bounded deadline, reports workers that remain alive, and must not start duplicate workers while a previous generation is still running.
- Queue policy must distinguish latest-quote coalescing from loss-sensitive events. Never coalesce order lifecycle events or other loss-sensitive records.

## Rollout sequence
1. Preserve current Angel shared three-socket admission and Shoonya's independent shadow-only lifecycle.
2. Add the canonical ingress envelope and bounded acquisition-to-processing hand-off behind a disabled-by-default feature flag.
3. Add the processing engine and per-consumer isolation; keep the current synchronous path as rollback until parity tests pass.
4. Run deterministic replay/parity tests against existing Box and IV outputs. Do not change scanner formulas or trading rules.
5. Enable shadow comparison only; compare raw receive counts, normalized records, timestamps, quote fields, freshness and consumer delivery.
6. Promote only after stress tests prove no silent loss, stale-generation delivery, duplicate workers or cross-broker coupling.

## Acceptance gates
- Acquisition thread remains responsive while a calculation consumer is deliberately blocked.
- A slow Box consumer does not delay IV delivery, and vice versa.
- A Shoonya disconnect does not affect Angel sockets; an Angel reconnect does not affect Shoonya.
- Queue saturation is visible and degrades the relevant feed instead of silently losing ticks.
- Per-instrument ordering and initial-snapshot requirements remain correct across reconnect.
- Existing Box/IV scanner outputs are unchanged in parity replay.
- CI passes before merge; no production deployment or live broker activation is inferred from CI.

## Explicitly out of scope
- Changing Box Spread or IV Scanner formulas, thresholds, entry/exit rules or subscriptions.
- Enabling Shoonya as a live scanner data source or order broker.
- Enabling Uvicorn multi-worker ownership before broker singleton ownership is externalized.
- Combining the two broker socket supervisors into one shared socket lifecycle.
