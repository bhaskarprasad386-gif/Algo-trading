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
    """Persist immutable callback evidence and apply its fill only when ordered.

    A cumulative-quantity gap is retained as a durable deferred event instead
    of being discarded. Replaying that exact event after earlier fills arrive
    retries application. Contradictory over-counts and invalid order identities
    still fail closed. No fill is inferred from cumulative quantities.
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
        if not has_any_fill_metadata:
            return {
                "event_created": event_result["created"],
                "fill_created": False,
                "fill_deferred": False,
                "event_record_id": event_result["event_record_id"],
                "fill_result": None,
            }

        broker = event.broker.strip().lower()
        account_id = event.account_id.strip()
        fill_id = event.fill_id.strip()
        order_id = event.broker_order_id.strip()

        # Exact fill identity replay is idempotent even if it arrived under a
        # second callback identity. Changed evidence under the same fill ID is
        # a hard conflict and must never change the immutable ledger.
        existing_fill = db.execute(select(ExecutionFillRecord).where(
            ExecutionFillRecord.account_id == account_id,
            ExecutionFillRecord.broker == broker,
            ExecutionFillRecord.broker_fill_id == fill_id,
        )).scalar_one_or_none()
        if existing_fill is not None:
            same_fill = (
                existing_fill.broker_order_id == order_id
                and existing_fill.quantity == event.fill_quantity
                and existing_fill.fill_price == event.average_fill_price
                and existing_fill.occurred_at_ns == event.occurred_at_ns
            )
            if not same_fill:
                raise BrokerEventConflict(
                    "broker fill identity was replayed with different execution evidence"
                )
            return {
                "event_created": event_result["created"],
                "fill_created": False,
                "fill_deferred": False,
                "event_record_id": event_result["event_record_id"],
                "fill_result": None,
            }

        order = db.execute(select(ExecutionBrokerOrderRecord).where(
            ExecutionBrokerOrderRecord.account_id == account_id,
            ExecutionBrokerOrderRecord.broker == broker,
            ExecutionBrokerOrderRecord.broker_order_id == order_id,
        ).with_for_update()).scalar_one_or_none()
        if order is None:
            raise LookupError("broker order is not registered for this account")
        if (
            event.fill_quantity > order.quantity
            or event.cumulative_filled_quantity > order.quantity
        ):
            raise ValueError("broker fill or cumulative quantity exceeds registered broker order quantity")

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
            ExecutionFillRecord.broker_order_id == order_id,
        )).scalar_one()
        expected_cumulative = previously_recorded + event.fill_quantity
        if expected_cumulative > event.cumulative_filled_quantity:
            raise ValueError(
                "callback cumulative fill quantity is below already persisted fills plus this fill"
            )
        if expected_cumulative < event.cumulative_filled_quantity:
            return {
                "event_created": event_result["created"],
                "fill_created": False,
                "fill_deferred": True,
                "defer_reason": "CUMULATIVE_FILL_GAP",
                "expected_cumulative_quantity": expected_cumulative,
                "broker_cumulative_quantity": event.cumulative_filled_quantity,
                "event_record_id": event_result["event_record_id"],
                "fill_result": None,
            }
        if (
            event.status.value == "FILLED"
            and event.cumulative_filled_quantity != order.quantity
        ):
            return {
                "event_created": event_result["created"],
                "fill_created": False,
                "fill_deferred": True,
                "defer_reason": "FILLED_STATUS_QUANTITY_CONFLICT",
                "event_record_id": event_result["event_record_id"],
                "fill_result": None,
            }

        try:
            fill_result = record_fill(
                db,
                account_id=account_id,
                intent_id=intent.intent_id,
                leg_id=leg.leg_id,
                broker=broker,
                broker_fill_id=fill_id,
                broker_order_id=order_id,
                quantity=event.fill_quantity,
                fill_price=event.average_fill_price,
                occurred_at_ns=event.occurred_at_ns,
            )
        except ValueError as exc:
            # Keep the immutable callback as evidence while leaving execution
            # progress untouched. A later explicit reconciliation may resolve
            # the blocked condition; this function never resumes an intent.
            return {
                "event_created": event_result["created"],
                "fill_created": False,
                "fill_deferred": True,
                "defer_reason": "FILL_APPLICATION_BLOCKED",
                "detail": str(exc),
                "event_record_id": event_result["event_record_id"],
                "fill_result": None,
            }
        if not fill_result["created"]:
            return {
                "event_created": event_result["created"],
                "fill_created": False,
                "fill_deferred": False,
                "event_record_id": event_result["event_record_id"],
                "fill_result": fill_result,
            }
        return {
            "event_created": event_result["created"],
            "fill_created": True,
            "fill_deferred": False,
            "event_record_id": event_result["event_record_id"],
            "fill_result": fill_result,
        }


def review_unapplied_broker_fill_events(
    db: Session, *, broker: str, account_id: str, limit: int = 500,
) -> dict:
    """Read-only inventory of trade-identified callback evidence not in the fill ledger.

    This is an operator-facing crash/reconnect review primitive. It never
    replays events, writes fills, changes intent state, or calls a broker.
    A missing exact fill identity is reported as pending evidence; an identity
    present with different execution fields is reported as a conflict.
    """
    broker = broker.strip().lower()
    account_id = account_id.strip()
    if not broker or not account_id:
        raise ValueError("broker and account_id are required")
    if limit < 1 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")

    events = db.execute(
        select(BrokerOrderEventRecord)
        .where(
            BrokerOrderEventRecord.broker == broker,
            BrokerOrderEventRecord.account_id == account_id,
            BrokerOrderEventRecord.fill_id.is_not(None),
            BrokerOrderEventRecord.fill_quantity.is_not(None),
            BrokerOrderEventRecord.average_fill_price.is_not(None),
        )
        .order_by(BrokerOrderEventRecord.occurred_at_ns, BrokerOrderEventRecord.id)
        .limit(limit + 1)
    ).scalars().all()
    truncated = len(events) > limit
    events = events[:limit]

    fill_ids = {row.fill_id for row in events if row.fill_id}
    fills = db.execute(
        select(ExecutionFillRecord).where(
            ExecutionFillRecord.broker == broker,
            ExecutionFillRecord.account_id == account_id,
            ExecutionFillRecord.broker_fill_id.in_(fill_ids) if fill_ids else False,
        )
    ).scalars().all()
    fills_by_id = {row.broker_fill_id: row for row in fills}

    pending = []
    conflicts = []
    applied_count = 0
    for row in events:
        fill = fills_by_id.get(row.fill_id)
        if fill is None:
            pending.append({
                "event_id": row.event_id,
                "broker_order_id": row.broker_order_id,
                "fill_id": row.fill_id,
                "fill_quantity": row.fill_quantity,
                "cumulative_filled_quantity": row.cumulative_filled_quantity,
                "occurred_at_ns": row.occurred_at_ns,
                "reason": "NO_MATCHING_PERSISTED_FILL",
            })
            continue
        exact = (
            fill.broker_order_id == row.broker_order_id
            and fill.quantity == row.fill_quantity
            and fill.fill_price == row.average_fill_price
            and fill.occurred_at_ns == row.occurred_at_ns
        )
        if exact:
            applied_count += 1
        else:
            conflicts.append({
                "event_id": row.event_id,
                "broker_order_id": row.broker_order_id,
                "fill_id": row.fill_id,
                "reason": "FILL_IDENTITY_EXECUTION_EVIDENCE_MISMATCH",
            })

    return {
        "broker": broker,
        "account_id": account_id,
        "scanned_event_count": len(events),
        "applied_event_count": applied_count,
        "pending_event_count": len(pending),
        "conflict_event_count": len(conflicts),
        "pending_events": pending,
        "conflict_events": conflicts,
        "truncated": truncated,
        "read_only": True,
    }
