"""Durable, idempotent persistence for normalized broker callbacks."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.execution.broker_events import BrokerOrderEvent
from app.models.broker_order_event import BrokerOrderEventRecord


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

    with db.begin():
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
