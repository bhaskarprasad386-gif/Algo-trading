"""Non-mutating eligibility preview for an explicitly selected deferred fill event."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.broker_order_event import BrokerOrderEventRecord
from app.models.execution_intent import (
    ExecutionBrokerOrderRecord,
    ExecutionFillRecord,
    ExecutionIntentRecord,
    ExecutionLegRecord,
)


def preview_deferred_fill_replay(
    db: Session, *, broker: str, account_id: str, event_id: str,
) -> dict:
    """Assess one exact callback event; never replay or mutate execution state."""
    broker_key = str(broker or "").strip().lower()
    account_key = str(account_id or "").strip()
    event_key = str(event_id or "").strip()
    if not broker_key or not account_key or not event_key:
        raise ValueError("broker, account_id and event_id are required")

    event = db.execute(select(BrokerOrderEventRecord).where(
        BrokerOrderEventRecord.broker == broker_key,
        BrokerOrderEventRecord.account_id == account_key,
        BrokerOrderEventRecord.event_id == event_key,
    )).scalar_one_or_none()
    if event is None:
        return {
            "status": "BLOCKED", "reason": "EVENT_NOT_FOUND",
            "trading_authorized": False, "replay_performed": False,
        }

    if (
        event.status not in {"PARTIALLY_FILLED", "FILLED"}
        or not event.fill_id
        or event.fill_quantity is None
        or event.average_fill_price is None
        or event.fill_quantity <= 0
        or event.cumulative_filled_quantity <= 0
    ):
        return {
            "status": "BLOCKED", "reason": "INCOMPLETE_FILL_EVIDENCE",
            "event_id": event.event_id, "trading_authorized": False,
            "replay_performed": False,
        }

    existing_fill = db.execute(select(ExecutionFillRecord).where(
        ExecutionFillRecord.account_id == account_key,
        ExecutionFillRecord.broker == broker_key,
        ExecutionFillRecord.broker_fill_id == event.fill_id,
    )).scalar_one_or_none()
    if existing_fill is not None:
        same = (
            existing_fill.broker_order_id == event.broker_order_id
            and existing_fill.quantity == event.fill_quantity
            and float(existing_fill.fill_price) == float(event.average_fill_price)
            and existing_fill.occurred_at_ns == event.occurred_at_ns
        )
        return {
            "status": "ALREADY_APPLIED" if same else "BLOCKED",
            "reason": "EXACT_FILL_ALREADY_RECORDED" if same else "FILL_IDENTITY_EVIDENCE_CONFLICT",
            "event_id": event.event_id, "trading_authorized": False,
            "replay_performed": False,
        }

    order = db.execute(select(ExecutionBrokerOrderRecord).where(
        ExecutionBrokerOrderRecord.account_id == account_key,
        ExecutionBrokerOrderRecord.broker == broker_key,
        ExecutionBrokerOrderRecord.broker_order_id == event.broker_order_id,
    )).scalar_one_or_none()
    if order is None:
        return {
            "status": "BLOCKED", "reason": "BROKER_ORDER_IDENTITY_UNRESOLVED",
            "event_id": event.event_id, "trading_authorized": False,
            "replay_performed": False,
        }

    intent = db.execute(select(ExecutionIntentRecord).where(
        ExecutionIntentRecord.id == order.intent_record_id,
        ExecutionIntentRecord.account_id == account_key,
    )).scalar_one_or_none()
    leg = db.get(ExecutionLegRecord, order.leg_record_id)
    if intent is None or leg is None:
        return {
            "status": "BLOCKED", "reason": "PERSISTED_INTENT_OR_LEG_MISSING",
            "event_id": event.event_id, "trading_authorized": False,
            "replay_performed": False,
        }

    prior_quantity = db.execute(select(
        func.coalesce(func.sum(ExecutionFillRecord.quantity), 0)
    ).where(
        ExecutionFillRecord.account_id == account_key,
        ExecutionFillRecord.broker == broker_key,
        ExecutionFillRecord.broker_order_id == event.broker_order_id,
    )).scalar_one()
    expected = int(prior_quantity) + event.fill_quantity
    if expected < event.cumulative_filled_quantity:
        reason = "CUMULATIVE_FILL_GAP"
    elif expected > event.cumulative_filled_quantity:
        reason = "CUMULATIVE_FILL_CONFLICT"
    elif event.fill_quantity > order.quantity or expected > order.quantity:
        reason = "REGISTERED_ORDER_QUANTITY_EXCEEDED"
    elif (
        event.status == "FILLED"
        and event.cumulative_filled_quantity != order.quantity
    ):
        reason = "FILLED_STATUS_QUANTITY_CONFLICT"
    else:
        reason = "CONTIGUOUS_FILL_EVIDENCE"

    ready = reason == "CONTIGUOUS_FILL_EVIDENCE"
    return {
        "status": "ELIGIBLE_FOR_EXPLICIT_OPERATOR_REVIEW" if ready else "BLOCKED",
        "reason": reason,
        "event_id": event.event_id,
        "broker_order_id": event.broker_order_id,
        "fill_id": event.fill_id,
        "persisted_fill_quantity": int(prior_quantity),
        "callback_fill_quantity": event.fill_quantity,
        "expected_cumulative_quantity": expected,
        "broker_cumulative_quantity": event.cumulative_filled_quantity,
        "registered_order_quantity": order.quantity,
        "intent_id": intent.intent_id,
        "leg_id": leg.leg_id,
        "trading_authorized": False,
        "replay_performed": False,
    }
