# VPT-X Reversal Scanner API and lifecycle

This API is snapshot-in/evaluate-only and intentionally has no live subscription, broker connection, paper execution, or order routing.

- `GET /api/v1/scanner/vpt-x/status`: status and isolation contract.
- `POST /api/v1/scanner/vpt-x/evaluate`: provide up to 500 timestamped ticks for NIFTY/BANKNIFTY.
- `GET /api/v1/scanner/vpt-x/signals`: bounded recent signal records.

Lifecycle states are WATCH and TRIGGERED; a triggered signal can become INVALIDATED when the supplied price crosses its engine invalidation level. The process-local record store is bounded (300 records) and expires records after 180 seconds. This is not durable history. Feed wiring is deliberately excluded until separately approved.
