"""Durable, idempotent persistence for normalized broker callbacks."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.execution.broker_events import BrokerOrderEvent
from app.models.broker_order_event import BrokerOrderEventRecord
from app.models.execution_intent import ExecutionBrokerOrderRecord, ExecutionFillRecord, ExecutionIntentRecord, ExecutionLegRecord
from app.execution.persistence import _transaction, record_fill


class BrokerEventConflict(ValueError):
    """A broker reused an event identity with a different payload."""


def _find_event(db: Session, *, broker: str, account_id: str, event_id: str):
    return db.execute(select(BrokerOrderEventRecord).where(
        BrokerOrderEventRecord.broker == broker,
        BrokerOrderEventRecord.account_id == account_id,
        BrokerOrderEventRecord.event_id == event_id,
    )).scalar_one_or_none()


def record_broker_order_event(db: Session, event: BrokerOrderEvent) -> dict:
    """Persist an immutable callback; exact concurrent replays are no-ops.

    The database unique constraint is the cross-worker identity arbiter. A
    savepoint contains a uniqueness race without poisoning the caller's outer
    transaction. This function records evidence only; it does not mutate fills.
    """
    payload = {
        "broker": event.broker.strip().lower(),
        "account_id": event.account_id.strip(),
        "event_id": event.event_id.strip(),
        "broker_order_id": event.broker_order_id.strip(),
        "status": event.status.value,
        "cumulative_filled_quantity": event.cumulative_filled_quantity,
        "fill_id": event.fill_id.strip() if event.fill_id else None,
        "fill_quantity": event.fill_quantity,
        "average_fill_price": event.average_fill_price,
        "occurred_at_ns": event.occurred_at_ns,
    }
    if not payload["broker"] or not payload["account_id"]:
        raise ValueError("broker and account_id are required")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    with _transaction(db):
        existing = _find_event(
            db, broker=payload["broker"], account_id=payload["account_id"],
            event_id=payload["event_id"],
        )
        if existing is not None:
            if existing.payload_hash != digest:
                raise BrokerEventConflict(
                    "broker event identity was replayed with a different payload"
                )
            return {"created": False, "event_record_id": existing.id}

        record = BrokerOrderEventRecord(
            **payload, payload_hash=digest, payload_json=canonical,
        )
        try:
            with db.begin_nested():
                db.add(record)
                db.flush()
            return {"created": True, "event_record_id": record.id}
        except IntegrityError:
            # Another worker may have inserted this unique identity after our
            # initial SELECT. The savepoint rollback leaves the outer session
            # usable so we can classify the winner's payload deterministically.
            existing = _find_event(
                db, broker=payload["broker"], account_id=payload["account_id"],
                event_id=payload["event_id"],
            )
            if existing is None:
                raise
            if existing.payload_hash != digest:
                raise BrokerEventConflict(
                    "broker event identity was replayed with a different payload"
                )
            return {"created": False, "event_record_id": existing.id}



def record_broker_order_event_with_fill(db: Session, event: BrokerOrderEvent) -> dict:
    """Atomically persist one callback and, when fully identified, its fill.

    Exact duplicate callbacks are no-ops and never call record_fill. Fill-bearing
    callbacks must include fill ID, positive quantity and average price, and map
    to a persisted broker order in the same account. Any validation/persistence
    error rolls back both the callback row and fill/progress mutation.
    """
    fill_metadata = (event.fill_id, event.fill_quantity, event.average_fill_price)
    has_any_fill_metadata = any(value is not None for value in fill_metadata)
    is_fill_status = event.status.value in {"PARTIALLY_FILLED", "FILLED"}
    if (has_any_fill_metadata or is_fill_status) and not all(
        value is not None for value in fill_metadata
    ):
        raise ValueError(
            "fill callback requires fill_id, fill_quantity and average_fill_price"
        )

    with _transaction(db):
        event_result = record_broker_order_event(db, event)
        if not event_result["created"]:
            return {
                "event_created": False,
                "fill_created": False,
                "event_record_id": event_result["event_record_id"],
                "fill_result": None,
            }

        if not has_any_fill_metadata:
            return {
                "event_created": True,
                "fill_created": False,
                "event_record_id": event_result["event_record_id"],
                "fill_result": None,
            }

        broker = event.broker.strip().lower()
        account_id = event.account_id.strip()
        order = db.execute(select(ExecutionBrokerOrderRecord).where(
            ExecutionBrokerOrderRecord.account_id == account_id,
            ExecutionBrokerOrderRecord.broker == broker,
            ExecutionBrokerOrderRecord.broker_order_id == event.broker_order_id.strip(),
        ).with_for_update()).scalar_one_or_none()
        if order is None:
            raise LookupError("broker order is not registered for this account")
        intent = db.execute(select(ExecutionIntentRecord).where(
            ExecutionIntentRecord.id == order.intent_record_id,
            ExecutionIntentRecord.account_id == account_id,
        )).scalar_one_or_none()
        if intent is None:
            raise LookupError("execution intent for broker order was not found")

        leg = db.get(ExecutionLegRecord, order.leg_record_id)
        if leg is None:
            raise LookupError("execution leg for broker order was not found")
        previously_recorded = db.execute(select(
            func.coalesce(func.sum(ExecutionFillRecord.quantity), 0)
        ).where(
            ExecutionFillRecord.account_id == account_id,
            ExecutionFillRecord.broker == broker,
            ExecutionFillRecord.broker_order_id == event.broker_order_id.strip(),
        )).scalar_one()
        if previously_recorded + event.fill_quantity != event.cumulative_filled_quantity:
            raise ValueError(
                "callback cumulative fill quantity does not match persisted fills plus this fill"
            )
        fill_result = record_fill(
            db,
            account_id=account_id,
            intent_id=intent.intent_id,
            leg_id=leg.leg_id,
            broker=broker,
            broker_fill_id=event.fill_id,
            broker_order_id=event.broker_order_id.strip(),
            quantity=event.fill_quantity,
            fill_price=event.average_fill_price,
            occurred_at_ns=event.occurred_at_ns,
        )
        if not fill_result["created"]:
            raise BrokerEventConflict(
                "fill identity was already consumed by a different callback event"
            )
        return {
            "event_created": True,
            "fill_created": fill_result["created"],
            "event_record_id": event_result["event_record_id"],
            "fill_result": fill_result,
        }
