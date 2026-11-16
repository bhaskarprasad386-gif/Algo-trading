"""Account-scoped, read-only broker tradebook -> immutable execution fill sync.

Only trade rows with a registered broker order, unique broker trade ID,
positive execution price and trustworthy execution timestamp can mutate the
local fill ledger. The whole snapshot is validated before any fill is written.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.execution.persistence import _transaction, record_fill
from app.models.execution_intent import (
    ExecutionBrokerOrderRecord,
    ExecutionIntentRecord,
    ExecutionLegRecord,
)


def _blocked(reason: str, *, trade_count: int = 0) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "reason": reason,
        "trade_count": trade_count,
        "created_count": 0,
        "duplicate_count": 0,
        "trading_authorized": False,
    }


def sync_account_tradebook_fills(
    db: Session, *, adapter: Any, account_id: str,
) -> dict[str, Any]:
    """Fetch one complete current-day snapshot and atomically persist trade fills.

    This is evidence ingestion only. It never submits, cancels, retries, adopts
    or resumes orders. Unknown/unregistered trades are blocked for operator
    reconciliation instead of being attached to a guessed execution leg.
    """
    broker = str(getattr(adapter, "name", "") or "").strip().lower()
    account = str(account_id or "").strip()
    if not broker or not account:
        raise ValueError("broker and account_id are required")
    if not bool(getattr(adapter, "connected", False)):
        return _blocked("BROKER_SESSION_NOT_CONNECTED")
    if str(getattr(adapter, "account_id", "") or "").strip() != account:
        return _blocked("BROKER_SESSION_ACCOUNT_MISMATCH")

    fetch = getattr(adapter, "fetch_today_broker_snapshot", None)
    if not callable(fetch):
        return _blocked("BROKER_HISTORY_UNAVAILABLE")
    history = fetch(account_id=account)
    snapshot = getattr(history, "snapshot", None)
    if getattr(history, "scope", None) != "CURRENT_TRADING_DAY" or snapshot is None:
        return _blocked("BROKER_HISTORY_SCOPE_UNSUPPORTED")
    if (
        getattr(snapshot, "complete", False) is not True
        or getattr(snapshot, "account_id", None) != account
        or str(getattr(snapshot, "broker", "") or "").strip().lower() != broker
    ):
        return _blocked("BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH")

    orders = tuple(snapshot.orders)
    trades = tuple(snapshot.trades)
    order_ids = [str(row.broker_order_id).strip() for row in orders]
    trade_ids = [str(row.broker_trade_id).strip() for row in trades]
    if any(not value for value in order_ids + trade_ids):
        return _blocked("BROKER_HISTORY_CONTAINS_EMPTY_IDENTITIES", trade_count=len(trades))
    if len(order_ids) != len(set(order_ids)):
        return _blocked("DUPLICATE_BROKER_ORDER_ID", trade_count=len(trades))
    if len(trade_ids) != len(set(trade_ids)):
        return _blocked("DUPLICATE_BROKER_TRADE_ID", trade_count=len(trades))

    order_by_id = {str(row.broker_order_id).strip(): row for row in orders}
    trade_totals: dict[str, int] = {}
    for trade in trades:
        order_id = str(trade.broker_order_id).strip()
        if order_id not in order_by_id:
            return _blocked("TRADE_REFERENCES_UNKNOWN_BROKER_ORDER", trade_count=len(trades))
        trade_totals[order_id] = trade_totals.get(order_id, 0) + int(trade.quantity)

    for order_id, order in order_by_id.items():
        if trade_totals.get(order_id, 0) != int(order.filled_quantity):
            return _blocked("ORDER_TRADE_FILL_TOTAL_MISMATCH", trade_count=len(trades))

    if not trades:
        return {
            "status": "SYNCED",
            "trading_date": getattr(history, "trading_date", None),
            "trade_count": 0,
            "created_count": 0,
            "duplicate_count": 0,
            "trading_authorized": False,
        }

    # Load account/broker order mappings once. Trades are never linked by
    # symbol/side/quantity heuristics; broker-assigned order identity is required.
    mapped_rows = db.execute(
        select(ExecutionBrokerOrderRecord, ExecutionIntentRecord, ExecutionLegRecord)
        .join(ExecutionIntentRecord, ExecutionIntentRecord.id == ExecutionBrokerOrderRecord.intent_record_id)
        .join(ExecutionLegRecord, ExecutionLegRecord.id == ExecutionBrokerOrderRecord.leg_record_id)
        .where(
            ExecutionBrokerOrderRecord.account_id == account,
            ExecutionBrokerOrderRecord.broker == broker,
            ExecutionBrokerOrderRecord.broker_order_id.in_(order_by_id),
        )
    ).all()
    mapped = {
        row[0].broker_order_id: (row[0], row[1], row[2])
        for row in mapped_rows
    }

    prepared: list[tuple[Any, Any, Any, Any, Decimal, int]] = []
    for trade in trades:
        order_id = str(trade.broker_order_id).strip()
        registered = mapped.get(order_id)
        if registered is None:
            return _blocked("UNREGISTERED_BROKER_ORDER", trade_count=len(trades))
        broker_order, intent, leg = registered
        broker_order_snapshot = order_by_id[order_id]
        if (
            broker_order.symbol != broker_order_snapshot.symbol
            or broker_order.side != broker_order_snapshot.side
            or broker_order.quantity != broker_order_snapshot.quantity
        ):
            return _blocked("REGISTERED_BROKER_ORDER_MISMATCH", trade_count=len(trades))
        if trade.fill_price is None or trade.occurred_at_ns is None:
            return _blocked("TRADE_EXECUTION_PRICE_OR_TIMESTAMP_MISSING", trade_count=len(trades))
        try:
            price = Decimal(str(trade.fill_price))
        except (InvalidOperation, TypeError, ValueError):
            return _blocked("TRADE_EXECUTION_PRICE_INVALID", trade_count=len(trades))
        if not price.is_finite() or price <= 0 or int(trade.quantity) <= 0 or int(trade.occurred_at_ns) <= 0:
            return _blocked("TRADE_EXECUTION_EVIDENCE_INVALID", trade_count=len(trades))
        prepared.append((trade, broker_order, intent, leg, price, int(trade.occurred_at_ns)))

    created = 0
    with _transaction(db):
        for trade, broker_order, intent, leg, price, occurred_at_ns in prepared:
            result = record_fill(
                db,
                account_id=account,
                intent_id=intent.intent_id,
                leg_id=leg.leg_id,
                broker=broker,
                broker_fill_id=str(trade.broker_trade_id).strip(),
                broker_order_id=broker_order.broker_order_id,
                quantity=int(trade.quantity),
                fill_price=price,
                occurred_at_ns=occurred_at_ns,
            )
            created += int(bool(result.get("created")))

    return {
        "status": "SYNCED",
        "trading_date": getattr(history, "trading_date", None),
        "trade_count": len(trades),
        "created_count": created,
        "duplicate_count": len(trades) - created,
        "trading_authorized": False,
    }
