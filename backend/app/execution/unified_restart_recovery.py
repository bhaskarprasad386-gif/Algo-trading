"""Unified, read-only startup inventory and atomic fail-closed freeze.

The service links the coordinator and recovery journals by the stable
(account_id, intent_id) pair. It never contacts a broker, replays an order,
clears a kill switch, or enables Live routing.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.execution.emergency_recovery import RecoveryState
from app.models.basket_execution_journal import (
    BasketExecutionJournalEventRecord,
    BasketExecutionJournalRecord,
)
from app.models.execution_recovery_journal import (
    RecoveryControlRecord,
    RecoveryJournalEventRecord,
    RecoveryKillSwitchRecord,
)
from app.models.execution_intent import ExecutionIntentRecord


_TERMINAL_INTENT = {"REJECTED", "COMPLETED"}
_TERMINAL_BASKET = {"PREFLIGHT_REJECTED", "FILLED"}
_FLAT = RecoveryState.FLAT.value


@dataclass(frozen=True)
class RestartRecoveryCandidate:
    account_id: str
    intent_id: str
    strategy: str
    basket_state: str | None
    execution_state: str | None
    recovery_state: str | None
    risk_reserved: bool
    frozen: bool
    updated_at_ns: int
    sources: tuple[str, ...]

    @property
    def requires_broker_reconciliation(self) -> bool:
        return bool(
            self.basket_state in {"CLAIMED", "SUBMITTING", "FROZEN", "MANUAL_REVIEW"}
            or self.execution_state not in (None, *_TERMINAL_INTENT)
            or (self.recovery_state not in (None, _FLAT) and self.risk_reserved)
        )


class UnifiedRestartRecoveryInventory:
    """Read-only cross-journal inventory plus an atomic freeze operation."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    def list_unresolved(self, *, account_id: str, limit: int = 500) -> tuple[RestartRecoveryCandidate, ...]:
        if not account_id.strip():
            raise ValueError("account_id is required")
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")

        with self._session_factory() as db:
            basket_rows = db.execute(select(BasketExecutionJournalRecord).where(
                BasketExecutionJournalRecord.account_id == account_id,
                BasketExecutionJournalRecord.state.not_in(_TERMINAL_BASKET),
            )).scalars().all()
            intent_rows = db.execute(select(ExecutionIntentRecord).where(
                ExecutionIntentRecord.account_id == account_id,
                ExecutionIntentRecord.status.not_in(_TERMINAL_INTENT),
            )).scalars().all()
            recovery_rows = db.execute(select(RecoveryControlRecord).where(
                RecoveryControlRecord.account_id == account_id,
                RecoveryControlRecord.state != _FLAT,
                RecoveryControlRecord.risk_reserved.is_(True),
            )).scalars().all()

            merged: dict[str, dict] = {}
            for row in basket_rows:
                item = merged.setdefault(row.intent_id, {
                    "account_id": account_id, "intent_id": row.intent_id,
                    "strategy": row.strategy, "basket_state": None,
                    "execution_state": None, "recovery_state": None,
                    "risk_reserved": False, "frozen": False,
                    "updated_at_ns": 0, "sources": set(),
                })
                item.update(basket_state=row.state, strategy=row.strategy)
                item["updated_at_ns"] = max(item["updated_at_ns"], row.updated_at_ns)
                item["sources"].add("basket")
            for row in intent_rows:
                item = merged.setdefault(row.intent_id, {
                    "account_id": account_id, "intent_id": row.intent_id,
                    "strategy": "UNKNOWN", "basket_state": None,
                    "execution_state": None, "recovery_state": None,
                    "risk_reserved": False, "frozen": False,
                    "updated_at_ns": 0, "sources": set(),
                })
                item["execution_state"] = row.status
                item["updated_at_ns"] = max(item["updated_at_ns"], row.updated_at_ns)
                item["sources"].add("execution")
            for row in recovery_rows:
                item = merged.setdefault(row.intent_id, {
                    "account_id": account_id, "intent_id": row.intent_id,
                    "strategy": row.strategy, "basket_state": None,
                    "execution_state": None, "recovery_state": None,
                    "risk_reserved": False, "frozen": False,
                    "updated_at_ns": 0, "sources": set(),
                })
                item.update(recovery_state=row.state, risk_reserved=row.risk_reserved,
                            frozen=row.frozen, strategy=row.strategy or item["strategy"])
                item["updated_at_ns"] = max(item["updated_at_ns"], row.updated_at_ns)
                item["sources"].add("recovery")

            ordered = sorted(merged.values(), key=lambda x: (x["updated_at_ns"], x["intent_id"]))
            return tuple(RestartRecoveryCandidate(
                account_id=x["account_id"], intent_id=x["intent_id"],
                strategy=x["strategy"], basket_state=x["basket_state"],
                execution_state=x["execution_state"], recovery_state=x["recovery_state"],
                risk_reserved=x["risk_reserved"], frozen=x["frozen"],
                updated_at_ns=x["updated_at_ns"], sources=tuple(sorted(x["sources"])),
            ) for x in ordered[:limit])

    def freeze_for_reconciliation(self, *, account_id: str, intent_id: str,
                                  reason: str) -> None:
        """Atomically freeze entry and latch its strategy kill switch.

        This operation is idempotent for an already-frozen intent. It does not
        claim that broker orders are cancelled or positions are flat.
        """
        if not account_id.strip() or not intent_id.strip() or not reason.strip():
            raise ValueError("account, intent and reason are required")
        now = time.time_ns()
        with self._session_factory() as db, db.begin():
            basket = db.execute(select(BasketExecutionJournalRecord).where(
                BasketExecutionJournalRecord.account_id == account_id,
                BasketExecutionJournalRecord.intent_id == intent_id,
            ).with_for_update()).scalar_one_or_none()
            execution = db.execute(select(ExecutionIntentRecord).where(
                ExecutionIntentRecord.account_id == account_id,
                ExecutionIntentRecord.intent_id == intent_id,
            ).with_for_update()).scalar_one_or_none()
            control = db.execute(select(RecoveryControlRecord).where(
                RecoveryControlRecord.account_id == account_id,
                RecoveryControlRecord.intent_id == intent_id,
            ).with_for_update()).scalar_one_or_none()

            if basket is None and execution is None and control is None:
                raise LookupError("no durable entry or recovery record exists for this account and intent")

            strategy = basket.strategy if basket else (control.strategy if control else "UNKNOWN")
            if execution is not None and basket is None:
                # The execution intent has no coordinator journal link. It is
                # still frozen at the account/intent boundary, but no order
                # state is rewritten without broker reconciliation evidence.
                strategy = strategy or "UNKNOWN"

            if basket is not None and basket.state not in _TERMINAL_BASKET:
                if basket.state != "FROZEN":
                    basket.state = "FROZEN"
                    basket.reason = reason
                    basket.version += 1
                    basket.updated_at_ns = now
                    seq = db.execute(select(func.coalesce(
                        func.max(BasketExecutionJournalEventRecord.sequence), -1
                    )).where(BasketExecutionJournalEventRecord.journal_id == basket.id)).scalar_one() + 1
                    db.add(BasketExecutionJournalEventRecord(
                        journal_id=basket.id, sequence=seq,
                        event_type="RESTART_RECONCILIATION_FREEZE",
                        payload_json=json.dumps({"reason": reason}, sort_keys=True, separators=(",", ":")),
                        occurred_at_ns=now,
                    ))

            if control is None:
                control = RecoveryControlRecord(
                    account_id=account_id, intent_id=intent_id,
                    strategy=strategy or "UNKNOWN", state=RecoveryState.RECOVERING.value,
                    frozen=True, risk_reserved=True, reason=reason, updated_at_ns=now,
                )
                db.add(control)
                db.flush()
            else:
                if control.state == _FLAT:
                    raise ValueError("verified FLAT recovery intent is terminal and cannot be reopened")
                control.frozen = True
                control.risk_reserved = True
                control.reason = reason
                control.state = RecoveryState.RECOVERING.value
                control.updated_at_ns = now

            seq = db.execute(select(func.coalesce(
                func.max(RecoveryJournalEventRecord.sequence), -1
            )).where(RecoveryJournalEventRecord.control_id == control.id)).scalar_one() + 1
            db.add(RecoveryJournalEventRecord(
                control_id=control.id, sequence=seq, event="RESTART_RECONCILIATION_REQUIRED",
                details_json=json.dumps({"reason": reason, "broker_action_taken": False},
                                        sort_keys=True, separators=(",", ":")),
                occurred_at_ns=now,
            ))

            kill = db.execute(select(RecoveryKillSwitchRecord).where(
                RecoveryKillSwitchRecord.account_id == account_id,
                RecoveryKillSwitchRecord.strategy == (strategy or "UNKNOWN"),
            ).with_for_update()).scalar_one_or_none()
            if kill is None:
                db.add(RecoveryKillSwitchRecord(
                    account_id=account_id, strategy=strategy or "UNKNOWN",
                    latched=True, reason=reason, latched_at_ns=now,
                ))
            else:
                kill.latched = True
                kill.reason = reason
