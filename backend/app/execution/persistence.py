"""Database-backed execution lifecycle; no broker or scanner side effects."""
from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.execution.contracts import ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide, validate_basket_legs
from app.models.execution_intent import ExecutionFillRecord, ExecutionIntentEventRecord, ExecutionIntentRecord, ExecutionLegRecord


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


def record_fill(
    db: Session, *, account_id: str, intent_id: str, leg_id: str,
    broker: str, broker_fill_id: str, broker_order_id: str,
    quantity: int, fill_price: Decimal | str | float, occurred_at_ns: int,
):
    """Record one immutable broker fill and update leg/intent progress atomically.

    Replayed identical broker fill IDs are idempotent. Reuse of a fill ID with
    different execution data is rejected. This records broker-reported fills;
    it does not submit, cancel, or reconcile orders with a broker.
    """
    broker = broker.strip().lower()
    broker_fill_id = broker_fill_id.strip()
    broker_order_id = broker_order_id.strip()
    price = Decimal(str(fill_price))
    if not account_id.strip() or not intent_id.strip() or not leg_id.strip():
        raise ValueError("account_id, intent_id and leg_id are required")
    if not broker or not broker_fill_id or not broker_order_id:
        raise ValueError("broker and broker execution identifiers are required")
    if quantity <= 0 or not price.is_finite() or price <= 0 or occurred_at_ns <= 0:
        raise ValueError("fill quantity, price and timestamp must be positive")
    canonical = json.dumps({
        "intent_id": intent_id, "leg_id": leg_id, "broker": broker,
        "broker_order_id": broker_order_id, "quantity": quantity,
        "fill_price": format(price, "f"), "occurred_at_ns": occurred_at_ns,
    }, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    with db.begin():
        duplicate = db.execute(select(ExecutionFillRecord).where(
            ExecutionFillRecord.account_id == account_id,
            ExecutionFillRecord.broker == broker,
            ExecutionFillRecord.broker_fill_id == broker_fill_id,
        )).scalar_one_or_none()
        if duplicate is not None:
            if duplicate.payload_hash != digest:
                raise IdempotencyConflict("broker fill ID was replayed with different execution data")
            return {"created": False, "filled_quantity": None, "intent_status": None}

        intent = db.execute(select(ExecutionIntentRecord).where(
            ExecutionIntentRecord.account_id == account_id,
            ExecutionIntentRecord.intent_id == intent_id,
        ).with_for_update()).scalar_one_or_none()
        if intent is None:
            raise LookupError("execution intent not found for account")
        leg = db.execute(select(ExecutionLegRecord).where(
            ExecutionLegRecord.intent_record_id == intent.id,
            ExecutionLegRecord.leg_id == leg_id,
        ).with_for_update()).scalar_one_or_none()
        if leg is None:
            raise LookupError("execution leg not found for intent")
        if intent.status not in {
            IntentStatus.SUBMITTING.value, IntentStatus.WORKING.value,
            IntentStatus.PARTIALLY_FILLED.value,
            IntentStatus.RECONCILIATION_REQUIRED.value,
        }:
            raise ValueError(f"fills are not accepted while intent is {intent.status}")
        if leg.filled_quantity + quantity > leg.quantity:
            raise ValueError("broker fill would exceed requested leg quantity")

        db.add(ExecutionFillRecord(
            intent_record_id=intent.id, leg_record_id=leg.id, account_id=account_id,
            broker=broker, broker_fill_id=broker_fill_id, broker_order_id=broker_order_id,
            quantity=quantity, fill_price=price, occurred_at_ns=occurred_at_ns,
            payload_hash=digest,
        ))
        leg.filled_quantity += quantity
        leg.status = "FILLED" if leg.filled_quantity == leg.quantity else "PARTIALLY_FILLED"

        legs = db.execute(select(ExecutionLegRecord).where(
            ExecutionLegRecord.intent_record_id == intent.id,
        ).with_for_update()).scalars().all()
        all_filled = all(x.filled_quantity == x.quantity for x in legs)
        any_filled = any(x.filled_quantity > 0 for x in legs)

        current = IntentStatus(intent.status)
        transitions = []
        if current is IntentStatus.SUBMITTING:
            transitions.append((IntentStatus.SUBMITTING, IntentStatus.WORKING))
            current = IntentStatus.WORKING
        if all_filled:
            target = IntentStatus.FILLED
        elif any_filled:
            target = IntentStatus.PARTIALLY_FILLED
        else:
            target = current
        if target is not current:
            # A late fill after restart may only be applied after an explicit
            # reconciliation gate; retain the same gate for state progression.
            if current is IntentStatus.RECONCILIATION_REQUIRED:
                raise ValueError("reconciliation must resolve intent before fill progress is applied")
            transitions.append((current, target))

        now = occurred_at_ns
        sequence = intent.version
        for from_status, to_status in transitions:
            if to_status not in {
                IntentStatus.WORKING, IntentStatus.PARTIALLY_FILLED, IntentStatus.FILLED,
            }:
                raise ValueError("unsupported fill-driven intent transition")
            db.add(ExecutionIntentEventRecord(
                intent_record_id=intent.id, sequence=sequence + 1,
                from_status=from_status.value, to_status=to_status.value,
                occurred_at_ns=now,
                details_json=json.dumps({
                    "event": "BROKER_FILL_RECORDED", "broker": broker,
                    "broker_fill_id": broker_fill_id, "leg_id": leg_id,
                    "fill_quantity": quantity,
                }, sort_keys=True, separators=(",", ":")),
            ))
            sequence += 1
        if transitions:
            intent.status = transitions[-1][1].value
            intent.version = sequence
            intent.updated_at_ns = now
        db.flush()
        return {
            "created": True,
            "filled_quantity": leg.filled_quantity,
            "leg_quantity": leg.quantity,
            "leg_status": leg.status,
            "intent_status": intent.status,
            "version": intent.version,
        }
