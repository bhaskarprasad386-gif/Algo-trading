"""Database-backed execution lifecycle; no broker or scanner side effects."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.execution.contracts import ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide, validate_basket_legs
from app.models.execution_intent import ExecutionIntentEventRecord, ExecutionIntentRecord, ExecutionLegRecord


class IdempotencyConflict(ValueError):
    pass


class ConcurrentTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersistedIntent:
    intent: ExecutionIntent
    legs: tuple[ExecutionLeg, ...]
    version: int


def _payload(legs):
    return tuple((x.leg_id, x.instrument, x.side.value, x.quantity, x.order_type, x.limit_price) for x in legs)


def _snapshot(record):
    legs = tuple(ExecutionLeg(
        leg_id=x.leg_id, instrument=x.instrument, side=OrderSide(x.side),
        quantity=x.quantity, order_type=x.order_type, limit_price=x.limit_price,
    ) for x in record.legs)
    intent = ExecutionIntent(
        intent_id=record.intent_id, account_id=record.account_id,
        idempotency_key=record.idempotency_key, correlation_id=record.correlation_id,
        mode=ExecutionMode(record.mode), status=IntentStatus(record.status),
        created_at_ns=record.created_at_ns,
    )
    return PersistedIntent(intent, legs, record.version)


def create_intent(db: Session, intent: ExecutionIntent, legs: tuple[ExecutionLeg, ...]):
    """Atomically persist intent, requested legs and initial event.

    Same account/key + same mode/legs returns the original record. A changed
    payload under that key is rejected. Call with a session without an open
    transaction; the unique constraint is the cross-worker idempotency arbiter.
    """
    if intent.status is not IntentStatus.CREATED:
        raise ValueError("new execution intents must start in CREATED state")
    validate_basket_legs(legs)
    wanted = _payload(legs)
    with db.begin():
        existing = db.execute(select(ExecutionIntentRecord)
            .options(selectinload(ExecutionIntentRecord.legs))
            .where(ExecutionIntentRecord.account_id == intent.account_id,
                   ExecutionIntentRecord.idempotency_key == intent.idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            found = tuple((x.leg_id, x.instrument, x.side, x.quantity, x.order_type, x.limit_price) for x in existing.legs)
            if existing.mode != intent.mode.value or found != wanted:
                raise IdempotencyConflict("idempotency key already belongs to a different execution request")
            return _snapshot(existing), False

        now = max(time.time_ns(), intent.created_at_ns)
        record = ExecutionIntentRecord(
            intent_id=intent.intent_id, account_id=intent.account_id,
            idempotency_key=intent.idempotency_key, correlation_id=intent.correlation_id,
            mode=intent.mode.value, status=intent.status.value,
            created_at_ns=intent.created_at_ns or now, updated_at_ns=now, version=0,
        )
        record.legs = [ExecutionLegRecord(
            leg_id=x.leg_id, instrument=x.instrument, side=x.side.value,
            quantity=x.quantity, order_type=x.order_type, limit_price=x.limit_price,
            status="PENDING", filled_quantity=0,
        ) for x in legs]
        record.events = [ExecutionIntentEventRecord(
            sequence=0, from_status=None, to_status=intent.status.value,
            occurred_at_ns=now, details_json='{"event":"INTENT_CREATED"}',
        )]
        db.add(record)
        db.flush()
        result = _snapshot(record)
    return result, True


def get_intent(db: Session, *, account_id: str, intent_id: str):
    with db.begin():
        record = db.execute(select(ExecutionIntentRecord)
            .options(selectinload(ExecutionIntentRecord.legs))
            .where(ExecutionIntentRecord.account_id == account_id,
                   ExecutionIntentRecord.intent_id == intent_id)
        ).scalar_one_or_none()
        return _snapshot(record) if record is not None else None


def transition_intent(db: Session, *, account_id: str, intent_id: str,
                      expected_version: int, target: IntentStatus,
                      occurred_at_ns: int | None = None, details: dict | None = None):
    """Validate and persist one transition plus immutable event atomically."""
    now = occurred_at_ns if occurred_at_ns is not None else time.time_ns()
    with db.begin():
        record = db.execute(select(ExecutionIntentRecord).where(
            ExecutionIntentRecord.account_id == account_id,
            ExecutionIntentRecord.intent_id == intent_id,
        ).with_for_update()).scalar_one_or_none()
        if record is None:
            raise LookupError("execution intent not found for account")
        if record.version != expected_version:
            raise ConcurrentTransitionError("execution intent version changed; reload before retrying")
        current = IntentStatus(record.status)
        next_state = ExecutionIntent(
            intent_id=record.intent_id, account_id=record.account_id,
            idempotency_key=record.idempotency_key, correlation_id=record.correlation_id,
            mode=ExecutionMode(record.mode), status=current, created_at_ns=record.created_at_ns,
        ).transition(target)
        result = db.execute(update(ExecutionIntentRecord).where(
            ExecutionIntentRecord.id == record.id,
            ExecutionIntentRecord.version == expected_version,
            ExecutionIntentRecord.status == current.value,
        ).values(status=next_state.status.value, version=expected_version + 1, updated_at_ns=now))
        if result.rowcount != 1:
            raise ConcurrentTransitionError("execution intent was concurrently modified")
        db.add(ExecutionIntentEventRecord(
            intent_record_id=record.id, sequence=expected_version + 1,
            from_status=current.value, to_status=target.value, occurred_at_ns=now,
            details_json=json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
        ))
        db.flush()
        refreshed = db.execute(select(ExecutionIntentRecord)
            .options(selectinload(ExecutionIntentRecord.legs))
            .where(ExecutionIntentRecord.id == record.id)).scalar_one()
        return _snapshot(refreshed)
