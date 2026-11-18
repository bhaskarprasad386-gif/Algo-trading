"""Read-only restart inventory for non-terminal execution intents.

This module deliberately does not replay orders, contact brokers, mutate intent
state, or enable Live execution. A caller must reconcile ambiguous broker state
before any execution can resume.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.execution.contracts import IntentStatus, LegProgress
from app.execution.persistence import PersistedIntent, _snapshot
from app.models.execution_intent import ExecutionIntentRecord, ExecutionLegRecord


_TERMINAL = (
    IntentStatus.REJECTED.value,
    IntentStatus.MANUAL_REVIEW.value,
    IntentStatus.COMPLETED.value,
)


@dataclass(frozen=True)
class RecoveryCandidate:
    """Persisted intent and leg progress requiring startup review."""

    persisted: PersistedIntent
    leg_progress: tuple[LegProgress, ...]

    @property
    def requires_broker_reconciliation(self) -> bool:
        return self.persisted.intent.status in {
            IntentStatus.SUBMITTING,
            IntentStatus.WORKING,
            IntentStatus.PARTIALLY_FILLED,
            IntentStatus.CANCEL_PENDING,
            IntentStatus.UNKNOWN,
            IntentStatus.RECONCILIATION_REQUIRED,
        }


def list_recovery_candidates(
    db: Session, *, account_id: str, limit: int = 500,
) -> tuple[RecoveryCandidate, ...]:
    """Return account-scoped non-terminal intents without changing any rows.

    The result is deterministic (oldest update first). The bounded limit avoids
    unbounded startup scans. No order replay or broker assumption is made.
    """
    if not account_id.strip():
        raise ValueError("account_id is required")
    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")

    with db.begin():
        records = db.execute(
            select(ExecutionIntentRecord)
            .options(selectinload(ExecutionIntentRecord.legs))
            .where(
                ExecutionIntentRecord.account_id == account_id,
                ExecutionIntentRecord.status.not_in(_TERMINAL),
            )
            .order_by(ExecutionIntentRecord.updated_at_ns, ExecutionIntentRecord.id)
            .limit(limit)
        ).scalars().all()

        return tuple(
            RecoveryCandidate(
                persisted=_snapshot(record),
                leg_progress=tuple(
                    LegProgress(
                        leg_id=leg.leg_id,
                        requested_quantity=leg.quantity,
                        filled_quantity=leg.filled_quantity,
                        status=leg.status,
                    )
                    for leg in record.legs
                ),
            )
            for record in records
        )
