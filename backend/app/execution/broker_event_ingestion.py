"""Authenticated adapter boundary for durable broker order-event ingestion.

Provider callbacks/polling decoders must normalize provider payloads into
BrokerOrderEvent and pass them through this function. No adapter may write the
ledger directly or infer an account from a callback payload.
"""
from __future__ import annotations

from typing import Any

from app.execution.broker_events import BrokerOrderEvent
from app.execution.broker_event_persistence import (
    record_broker_order_event,
    record_broker_order_event_with_fill,
)


class BrokerEventIngestionBlocked(ValueError):
    """The event cannot be safely attributed to the active broker session."""


def ingest_broker_order_event(
    db: Any,
    *,
    adapter: Any,
    event: BrokerOrderEvent,
) -> dict[str, Any]:
    """Validate active session identity, then durably ingest one normalized event.

    The adapter's authenticated identity is authoritative. Event-supplied broker
    or account values are assertions only and must match exactly after broker
    case normalization. The fill path atomically writes event + fill progress;
    status-only events write immutable event evidence only.
    """
    broker = str(getattr(adapter, "name", "") or "").strip().lower()
    account_id = str(getattr(adapter, "account_id", "") or "").strip()
    if not bool(getattr(adapter, "connected", False)):
        raise BrokerEventIngestionBlocked("broker session is not connected")
    if not broker or not account_id:
        raise BrokerEventIngestionBlocked("authenticated broker/account identity is unavailable")
    if event.broker.strip().lower() != broker:
        raise BrokerEventIngestionBlocked("event broker does not match authenticated adapter")
    if event.account_id.strip() != account_id:
        raise BrokerEventIngestionBlocked("event account does not match authenticated adapter")

    has_complete_fill_evidence = all(
        value is not None
        for value in (event.fill_id, event.fill_quantity, event.average_fill_price)
    )
    # Providers can send partial execution metadata (for example, a trade ID
    # and quantity before the average price is available). Preserve that
    # immutable callback as evidence, but do not enter the atomic fill path
    # until the full trade identity, quantity, and price are present.
    if has_complete_fill_evidence:
        return record_broker_order_event_with_fill(db, event)
    return record_broker_order_event(db, event)
