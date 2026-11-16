"""Read-only visibility for broker callback events awaiting fill reconciliation."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.broker_order_event import BrokerOrderEventRecord
from app.models.execution_intent import (
    ExecutionBrokerOrderRecord,
    ExecutionFillRecord,
)


def review_deferred_broker_events(
    db: Session, *, broker: str, account_id: str, limit: int = 200,
) -> dict:
    """Classify persisted callback events without applying or replaying fills."""
    broker_key = str(broker or "").strip().lower()
    account_key = str(account_id or "").strip()
    if not broker_key or not account_key:
        raise ValueError("broker and account_id are required")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")

    events = db.execute(
        select(BrokerOrderEventRecord).where(
            BrokerOrderEventRecord.broker == broker_key,
            BrokerOrderEventRecord.account_id == account_key,
        ).order_by(
            BrokerOrderEventRecord.occurred_at_ns.desc(),
            BrokerOrderEventRecord.id.desc(),
        ).limit(limit)
    ).scalars().all()

    fill_ids = {event.fill_id for event in events if event.fill_id}
    fills_by_id = {}
    if fill_ids:
        fills = db.execute(select(ExecutionFillRecord).where(
            ExecutionFillRecord.account_id == account_key,
            ExecutionFillRecord.broker == broker_key,
            ExecutionFillRecord.broker_fill_id.in_(fill_ids),
        )).scalars().all()
        fills_by_id = {fill.broker_fill_id: fill for fill in fills}

    order_ids = {event.broker_order_id for event in events}
    filled_by_order = {}
    order_quantities = {}
    if order_ids:
        totals = db.execute(
            select(
                ExecutionFillRecord.broker_order_id,
                func.coalesce(func.sum(ExecutionFillRecord.quantity), 0),
            ).where(
                ExecutionFillRecord.account_id == account_key,
                ExecutionFillRecord.broker == broker_key,
                ExecutionFillRecord.broker_order_id.in_(order_ids),
            ).group_by(ExecutionFillRecord.broker_order_id)
        ).all()
        filled_by_order = {str(order_id): int(quantity) for order_id, quantity in totals}
        orders = db.execute(select(ExecutionBrokerOrderRecord).where(
            ExecutionBrokerOrderRecord.account_id == account_key,
            ExecutionBrokerOrderRecord.broker == broker_key,
            ExecutionBrokerOrderRecord.broker_order_id.in_(order_ids),
        )).scalars().all()
        order_quantities = {order.broker_order_id: order.quantity for order in orders}

    unresolved = []
    for event in events:
        status = event.status
        if status in {"PARTIALLY_FILLED", "FILLED"} and (
            not event.fill_id or event.fill_quantity is None
            or event.average_fill_price is None
        ):
            reason = "FILL_IDENTITY_MISSING"
        elif not event.fill_id:
            continue
        else:
            fill = fills_by_id.get(event.fill_id)
            if fill is not None:
                if (
                    fill.broker_order_id != event.broker_order_id
                    or fill.quantity != event.fill_quantity
                    or event.average_fill_price is None
                    or float(fill.fill_price) != float(event.average_fill_price)
                    or fill.occurred_at_ns != event.occurred_at_ns
                ):
                    reason = "FILL_IDENTITY_EVIDENCE_CONFLICT"
                else:
                    continue
            elif event.broker_order_id not in order_quantities:
                reason = "BROKER_ORDER_IDENTITY_UNRESOLVED"
            else:
                prior_quantity = filled_by_order.get(event.broker_order_id, 0)
                expected = prior_quantity + int(event.fill_quantity or 0)
                if expected < event.cumulative_filled_quantity:
                    reason = "CUMULATIVE_FILL_GAP"
                elif expected > event.cumulative_filled_quantity:
                    reason = "CUMULATIVE_FILL_CONFLICT"
                elif (
                    status == "FILLED"
                    and event.cumulative_filled_quantity
                    != order_quantities[event.broker_order_id]
                ):
                    reason = "FILLED_STATUS_QUANTITY_CONFLICT"
                else:
                    reason = "FILL_APPLICATION_NOT_RECORDED"

        unresolved.append({
            "event_id": event.event_id,
            "broker_order_id": event.broker_order_id,
            "status": status,
            "reason": reason,
            "fill_id": event.fill_id,
            "fill_quantity": event.fill_quantity,
            "cumulative_filled_quantity": event.cumulative_filled_quantity,
            "occurred_at_ns": event.occurred_at_ns,
        })

    deferred_count = sum(
        1 for item in unresolved
        if item["reason"] == "CUMULATIVE_FILL_GAP"
    )
    return {
        "status": "CLEAR" if not unresolved else "REQUIRES_REVIEW",
        "broker": broker_key,
        "account_id": account_key,
        "event_count": len(events),
        "unresolved_count": len(unresolved),
        "deferred_count": deferred_count,
        "events": unresolved,
        "trading_authorized": False,
    }
