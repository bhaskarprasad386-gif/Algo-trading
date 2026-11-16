"""Read-only broker daily snapshot -> durable immutable order-status events.

This polling path is deliberately separate from market-data WebSockets. It
never manufactures trade fills: snapshots do not carry a trade execution price
and fill identity in the common contract, so only order status evidence is
recorded here.
"""
from __future__ import annotations

import hashlib
from contextlib import nullcontext
from typing import Any

from app.execution.broker_events import BrokerOrderEvent, BrokerOrderEventStatus
from app.execution.broker_event_persistence import record_broker_order_event
from app.execution.persistence import _transaction


_STATUS_ALIASES = {
    "NEW": BrokerOrderEventStatus.NEW,
    "PUT ORDER REQ RECEIVED": BrokerOrderEventStatus.NEW,
    "OPEN": BrokerOrderEventStatus.OPEN,
    "PENDING": BrokerOrderEventStatus.OPEN,
    "TRIGGER PENDING": BrokerOrderEventStatus.OPEN,
    "VALIDATION PENDING": BrokerOrderEventStatus.OPEN,
    "PARTIALLY FILLED": BrokerOrderEventStatus.PARTIALLY_FILLED,
    "PARTIAL FILLED": BrokerOrderEventStatus.PARTIALLY_FILLED,
    "PARTIAL": BrokerOrderEventStatus.PARTIALLY_FILLED,
    "COMPLETE": BrokerOrderEventStatus.FILLED,
    "COMPLETED": BrokerOrderEventStatus.FILLED,
    "FILLED": BrokerOrderEventStatus.FILLED,
    "TRADED": BrokerOrderEventStatus.FILLED,
    "CANCEL PENDING": BrokerOrderEventStatus.CANCEL_PENDING,
    "CANCELLED": BrokerOrderEventStatus.CANCELLED,
    "CANCELED": BrokerOrderEventStatus.CANCELLED,
    "REJECTED": BrokerOrderEventStatus.REJECTED,
}


def _normalized_status(raw_status: str | None, *, quantity: int, filled: int) -> BrokerOrderEventStatus:
    raw = " ".join(str(raw_status or "").strip().upper().replace("_", " ").replace("-", " ").split())
    status = _STATUS_ALIASES.get(raw, BrokerOrderEventStatus.UNKNOWN)
    # Quantity evidence is stronger than a generic OPEN/NEW label. Keep
    # terminal cancellation/rejection states intact even if partially filled.
    if status not in {BrokerOrderEventStatus.CANCELLED, BrokerOrderEventStatus.CANCEL_PENDING,
                      BrokerOrderEventStatus.REJECTED}:
        if filled > 0 and filled < quantity:
            return BrokerOrderEventStatus.PARTIALLY_FILLED
        if filled == quantity and quantity > 0:
            return BrokerOrderEventStatus.FILLED
        if status in {BrokerOrderEventStatus.PARTIALLY_FILLED, BrokerOrderEventStatus.FILLED} and filled == 0:
            return BrokerOrderEventStatus.UNKNOWN
    return status


def snapshot_order_to_event(*, broker: str, account_id: str, order: Any) -> BrokerOrderEvent:
    """Convert one validated common BrokerOrderSnapshot to status-only evidence."""
    broker_key = str(broker or "").strip().lower()
    account = str(account_id or "").strip()
    order_id = str(getattr(order, "broker_order_id", "") or "").strip()
    symbol = str(getattr(order, "symbol", "") or "").strip()
    side = str(getattr(order, "side", "") or "").strip().upper()
    quantity = getattr(order, "quantity", None)
    filled = getattr(order, "filled_quantity", None)
    if not broker_key or not account or not order_id or not symbol:
        raise ValueError("broker, account, order ID and symbol are required")
    if side not in {"BUY", "SELL"}:
        raise ValueError("broker snapshot order side is invalid")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("broker snapshot order quantity is invalid")
    if isinstance(filled, bool) or not isinstance(filled, int) or not 0 <= filled <= quantity:
        raise ValueError("broker snapshot filled quantity is invalid")

    raw_status = str(getattr(order, "raw_status", "") or "")
    status = _normalized_status(raw_status, quantity=quantity, filled=filled)
    identity_material = "|".join((
        broker_key, account, order_id, symbol, side, str(quantity), str(filled),
        " ".join(raw_status.strip().upper().split()),
    ))
    digest = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()
    return BrokerOrderEvent(
        broker=broker_key,
        account_id=account,
        event_id=f"snapshot:{digest}",
        broker_order_id=order_id,
        status=status,
        cumulative_filled_quantity=filled,
        # Snapshot order books do not prove trade identity or execution price.
        fill_id=None,
        fill_quantity=None,
        average_fill_price=None,
        occurred_at_ns=0,
    )


def sync_account_order_status_events(
    db: Any, *, adapter: Any, account_id: str,
) -> dict[str, Any]:
    """Fetch one complete current-day snapshot and persist its order status evidence.

    All events are committed together. Incomplete history or account/broker
    mismatch blocks before writes. Repeated identical snapshots are idempotent.
    """
    broker = str(getattr(adapter, "name", "") or "").strip().lower()
    account = str(account_id or "").strip()
    if not broker or not account:
        raise ValueError("broker and account_id are required")
    if not bool(getattr(adapter, "connected", False)):
        return {"status": "BLOCKED", "reason": "BROKER_SESSION_NOT_CONNECTED",
                "event_count": 0, "created_count": 0, "trading_authorized": False}
    if str(getattr(adapter, "account_id", "") or "").strip() != account:
        return {"status": "BLOCKED", "reason": "BROKER_SESSION_ACCOUNT_MISMATCH",
                "event_count": 0, "created_count": 0, "trading_authorized": False}
    fetch = getattr(adapter, "fetch_today_broker_snapshot", None)
    if not callable(fetch):
        return {"status": "BLOCKED", "reason": "BROKER_HISTORY_UNAVAILABLE",
                "event_count": 0, "created_count": 0, "trading_authorized": False}
    history = fetch(account_id=account)
    snapshot = getattr(history, "snapshot", None)
    if getattr(history, "scope", None) != "CURRENT_TRADING_DAY" or snapshot is None:
        return {"status": "BLOCKED", "reason": "BROKER_HISTORY_SCOPE_UNSUPPORTED",
                "event_count": 0, "created_count": 0, "trading_authorized": False}
    if (
        getattr(snapshot, "complete", False) is not True
        or getattr(snapshot, "account_id", None) != account
        or str(getattr(snapshot, "broker", "")).strip().lower() != broker
    ):
        return {"status": "BLOCKED", "reason": "BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH",
                "event_count": 0, "created_count": 0, "trading_authorized": False}

    events = tuple(
        snapshot_order_to_event(broker=broker, account_id=account, order=order)
        for order in snapshot.orders
    )
    created = 0
    # A malformed row or DB failure rolls back the whole snapshot batch.
    transaction = _transaction(db) if hasattr(db, "begin") else nullcontext()
    with transaction:
        for event in events:
            result = record_broker_order_event(db, event)
            created += int(bool(result.get("created")))
    return {
        "status": "SYNCED",
        "trading_date": getattr(history, "trading_date", None),
        "event_count": len(events),
        "created_count": created,
        "duplicate_count": len(events) - created,
        "trading_authorized": False,
    }
