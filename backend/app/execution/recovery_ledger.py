"""Read-only loader for durable per-intent broker recovery evidence.

This module only reads the execution ledger. It never contacts a broker,
changes intent state, adopts broker orders, or authorizes replay/resume.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.execution.recovery import RecoveryCandidate
from app.execution.recovery_review import LocalBrokerOrder
from app.models.execution_intent import (
    ExecutionBrokerOrderRecord,
    ExecutionFillRecord,
)


@dataclass(frozen=True)
class RecoveryLedgerEvidence:
    """Immutable local identities required to review exactly one intent."""

    broker: str
    local_orders: tuple[LocalBrokerOrder, ...]
    local_fill_quantities: dict[str, int]


def load_recovery_ledger_evidence(
    db: Session, *, candidate: RecoveryCandidate, broker: str,
) -> RecoveryLedgerEvidence:
    """Load one intent's persisted order/fill identities for a broker.

    The candidate is already account-scoped by list_recovery_candidates.
    Every selected row is additionally constrained by its intent record ID
    and account ID. Mixed-broker evidence is rejected rather than guessed.
    """
    broker_key = str(broker or "").strip().lower()
    if not broker_key:
        raise ValueError("broker is required")

    intent = candidate.persisted.intent
    account_id = intent.account_id
    if not account_id.strip() or not intent.intent_id.strip():
        raise ValueError("recovery candidate identity is incomplete")

    order_rows = db.execute(
        select(ExecutionBrokerOrderRecord).where(
            ExecutionBrokerOrderRecord.intent_record_id == _intent_record_id(db, candidate),
            ExecutionBrokerOrderRecord.account_id == account_id,
        ).order_by(ExecutionBrokerOrderRecord.id)
    ).scalars().all()
    fill_rows = db.execute(
        select(ExecutionFillRecord).where(
            ExecutionFillRecord.intent_record_id == _intent_record_id(db, candidate),
            ExecutionFillRecord.account_id == account_id,
        ).order_by(ExecutionFillRecord.id)
    ).scalars().all()

    brokers = {
        row.broker.strip().lower()
        for row in (*order_rows, *fill_rows)
        if row.broker and row.broker.strip()
    }
    if brokers and brokers != {broker_key}:
        raise ValueError("persisted recovery ledger broker does not match requested broker")

    local_orders = tuple(
        LocalBrokerOrder(
            leg_id=row.leg_record.leg_id if hasattr(row, "leg_record") else _leg_id(db, row.leg_record_id),
            broker_order_id=row.broker_order_id,
            symbol=row.symbol,
            side=row.side,
            quantity=row.quantity,
        )
        for row in order_rows
        if row.broker.strip().lower() == broker_key
    )
    fill_quantities: dict[str, int] = {}
    for row in fill_rows:
        if row.broker.strip().lower() != broker_key:
            continue
        if row.broker_fill_id in fill_quantities:
            raise ValueError("duplicate persisted broker fill identity in recovery ledger")
        fill_quantities[row.broker_fill_id] = row.quantity

    return RecoveryLedgerEvidence(
        broker=broker_key,
        local_orders=local_orders,
        local_fill_quantities=fill_quantities,
    )


def _intent_record_id(db: Session, candidate: RecoveryCandidate) -> int:
    """Resolve the durable row by both stable account and intent identities."""
    from app.models.execution_intent import ExecutionIntentRecord

    record = db.execute(
        select(ExecutionIntentRecord.id).where(
            ExecutionIntentRecord.account_id == candidate.persisted.intent.account_id,
            ExecutionIntentRecord.intent_id == candidate.persisted.intent.intent_id,
        )
    ).scalar_one_or_none()
    if record is None:
        raise LookupError("recovery candidate no longer exists in the execution ledger")
    return int(record)


def _leg_id(db: Session, leg_record_id: int) -> str:
    from app.models.execution_intent import ExecutionLegRecord

    leg_id = db.execute(
        select(ExecutionLegRecord.leg_id).where(ExecutionLegRecord.id == leg_record_id)
    ).scalar_one_or_none()
    if leg_id is None:
        raise LookupError("persisted broker order references a missing execution leg")
    return str(leg_id)
