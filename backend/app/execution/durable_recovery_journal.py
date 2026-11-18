"""SQLAlchemy-backed, fail-closed emergency recovery journal.

This is a persistence adapter only. It does not connect a broker, approve Live
routing, or grant a recovery budget implicitly. Leases and budgets must be
explicitly provisioned before recovery can close any residual position.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.execution.emergency_recovery import RecoveryState
from app.models.execution_recovery_journal import (
    RecoveryBudgetRecord,
    RecoveryControlRecord,
    RecoveryInstrumentLeaseRecord,
    RecoveryJournalEventRecord,
    RecoveryKillSwitchRecord,
)


class DurableRecoveryJournal:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    @staticmethod
    def _identity(account_id: str, intent_id: str) -> None:
        if not account_id.strip() or not intent_id.strip():
            raise ValueError("account and intent identity are required")

    def _control(self, db: Session, account_id: str, intent_id: str) -> RecoveryControlRecord:
        row = db.execute(select(RecoveryControlRecord).where(
            RecoveryControlRecord.account_id == account_id,
            RecoveryControlRecord.intent_id == intent_id,
        ).with_for_update()).scalar_one_or_none()
        if row is None:
            row = RecoveryControlRecord(
                account_id=account_id, intent_id=intent_id, strategy="UNKNOWN",
                state=RecoveryState.RECOVERING.value, frozen=False,
                risk_reserved=True, reason="", updated_at_ns=time.time_ns(),
            )
            db.add(row)
            db.flush()
        return row

    def _event(self, db: Session, control: RecoveryControlRecord,
               event: str, details: dict) -> None:
        sequence = db.execute(select(
            func.coalesce(func.max(RecoveryJournalEventRecord.sequence), -1)
        ).where(RecoveryJournalEventRecord.control_id == control.id)).scalar_one() + 1
        db.add(RecoveryJournalEventRecord(
            control_id=control.id, sequence=sequence, event=event,
            details_json=json.dumps(details, sort_keys=True, separators=(",", ":"), default=str),
            occurred_at_ns=time.time_ns(),
        ))
        control.updated_at_ns = time.time_ns()

    def configure_recovery_budget(self, *, account_id: str, intent_id: str,
                                  symbol: str, max_attempts: int,
                                  max_notional: Decimal, approved: bool) -> None:
        """Explicitly provision a bounded recovery budget; never silently enlarge it."""
        self._identity(account_id, intent_id)
        if not symbol.strip() or isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("symbol and positive attempt limit are required")
        if not max_notional.is_finite() or max_notional <= 0 or not isinstance(approved, bool):
            raise ValueError("a finite positive notional cap and explicit approval are required")
        with self._session_factory() as db, db.begin():
            existing = db.execute(select(RecoveryBudgetRecord).where(
                RecoveryBudgetRecord.account_id == account_id,
                RecoveryBudgetRecord.intent_id == intent_id,
                RecoveryBudgetRecord.symbol == symbol,
            ).with_for_update()).scalar_one_or_none()
            if existing is not None:
                if (existing.max_attempts != max_attempts
                    or Decimal(existing.max_notional) != max_notional
                    or existing.approved != approved):
                    raise ValueError("an existing recovery budget cannot be replaced or enlarged")
                return
            db.add(RecoveryBudgetRecord(
                account_id=account_id, intent_id=intent_id, symbol=symbol,
                approved=approved, max_attempts=max_attempts, attempts_used=0,
                max_notional=max_notional, notional_used=Decimal("0"),
                updated_at_ns=time.time_ns(),
            ))

    def acquire_instrument_leases(self, *, account_id: str, intent_id: str,
                                  symbols: tuple[str, ...]) -> bool:
        """Atomically reserve every basket instrument or reserve none."""
        self._identity(account_id, intent_id)
        normalized = tuple(sorted(set(symbol.strip() for symbol in symbols if symbol.strip())))
        if not normalized or len(normalized) != len(symbols):
            raise ValueError("unique non-empty basket symbols are required")
        with self._session_factory() as db:
            try:
                with db.begin():
                    for symbol in normalized:
                        db.add(RecoveryInstrumentLeaseRecord(
                            account_id=account_id, intent_id=intent_id,
                            symbol=symbol, acquired_at_ns=time.time_ns(),
                        ))
                    db.flush()
                return True
            except IntegrityError:
                return False

    def latch_entry_kill_switch(self, *, account_id: str, strategy: str, reason: str) -> None:
        if not account_id.strip() or not strategy.strip() or not reason.strip():
            raise ValueError("kill switch account, strategy and reason are required")
        with self._session_factory() as db, db.begin():
            row = db.execute(select(RecoveryKillSwitchRecord).where(
                RecoveryKillSwitchRecord.account_id == account_id,
                RecoveryKillSwitchRecord.strategy == strategy,
            ).with_for_update()).scalar_one_or_none()
            if row is None:
                db.add(RecoveryKillSwitchRecord(
                    account_id=account_id, strategy=strategy, latched=True,
                    reason=reason, latched_at_ns=time.time_ns(),
                ))
            else:
                row.latched = True
                row.reason = reason

    def is_entry_kill_switch_latched(self, *, account_id: str, strategy: str) -> bool:
        with self._session_factory() as db:
            row = db.execute(select(RecoveryKillSwitchRecord).where(
                RecoveryKillSwitchRecord.account_id == account_id,
                RecoveryKillSwitchRecord.strategy == strategy,
            )).scalar_one_or_none()
            return bool(row and row.latched)

    def freeze_intent(self, *, account_id: str, intent_id: str, reason: str) -> None:
        self._identity(account_id, intent_id)
        with self._session_factory() as db, db.begin():
            row = self._control(db, account_id, intent_id)
            row.frozen = True
            row.reason = reason
            self._event(db, row, "INTENT_FROZEN", {"reason": reason})

    def assert_exclusive_instrument_lease(self, *, account_id: str, intent_id: str,
                                          symbols: tuple[str, ...]) -> bool:
        expected = set(symbols)
        if not expected:
            return False
        with self._session_factory() as db:
            rows = db.execute(select(RecoveryInstrumentLeaseRecord).where(
                RecoveryInstrumentLeaseRecord.account_id == account_id,
                RecoveryInstrumentLeaseRecord.intent_id == intent_id,
            )).scalars().all()
            return {row.symbol for row in rows} == expected

    def append_event(self, *, account_id: str, intent_id: str,
                     event: str, details: dict) -> None:
        self._identity(account_id, intent_id)
        if not event.strip():
            raise ValueError("recovery event name is required")
        with self._session_factory() as db, db.begin():
            row = self._control(db, account_id, intent_id)
            self._event(db, row, event, details)

    def set_state(self, *, account_id: str, intent_id: str,
                  state: RecoveryState, reason: str) -> None:
        self._identity(account_id, intent_id)
        with self._session_factory() as db, db.begin():
            row = self._control(db, account_id, intent_id)
            if row.state == RecoveryState.FLAT.value and state is not RecoveryState.FLAT:
                raise ValueError("verified flat recovery state is terminal")
            row.state = state.value
            row.reason = reason
            self._event(db, row, "RECOVERY_STATE_CHANGED", {
                "state": state.value, "reason": reason,
            })

    def release_risk_reservation(self, *, account_id: str, intent_id: str) -> None:
        self._identity(account_id, intent_id)
        with self._session_factory() as db, db.begin():
            row = self._control(db, account_id, intent_id)
            if row.state != RecoveryState.FLAT.value:
                raise ValueError("risk reservation and instrument leases remain until broker-confirmed FLAT")
            row.risk_reserved = False
            leases = db.execute(select(RecoveryInstrumentLeaseRecord).where(
                RecoveryInstrumentLeaseRecord.account_id == account_id,
                RecoveryInstrumentLeaseRecord.intent_id == intent_id,
            )).scalars().all()
            for lease in leases:
                db.delete(lease)
            self._event(db, row, "RISK_RESERVATION_RELEASED", {"leases_released": len(leases)})

    def claim_recovery_attempt(self, *, account_id: str, intent_id: str, symbol: str) -> int:
        with self._session_factory() as db, db.begin():
            budget = db.execute(select(RecoveryBudgetRecord).where(
                RecoveryBudgetRecord.account_id == account_id,
                RecoveryBudgetRecord.intent_id == intent_id,
                RecoveryBudgetRecord.symbol == symbol,
            ).with_for_update()).scalar_one_or_none()
            if budget is None or not budget.approved:
                raise RuntimeError("explicit approved recovery budget is missing")
            if budget.attempts_used >= budget.max_attempts:
                raise RuntimeError("approved recovery attempt budget is exhausted")
            budget.attempts_used += 1
            budget.updated_at_ns = time.time_ns()
            control = self._control(db, account_id, intent_id)
            self._event(db, control, "RECOVERY_ATTEMPT_CLAIMED", {
                "symbol": symbol, "attempt": budget.attempts_used,
            })
            return budget.attempts_used

    def approve_emergency_close(self, *, account_id: str, intent_id: str,
                                symbol: str, side: str, quantity: int,
                                limit_price: Decimal, notional: Decimal) -> bool:
        if side not in {"BUY", "SELL"} or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            return False
        if not limit_price.is_finite() or limit_price <= 0 or not notional.is_finite() or notional <= 0:
            return False
        expected_notional = limit_price * quantity
        if notional != expected_notional:
            return False
        with self._session_factory() as db, db.begin():
            budget = db.execute(select(RecoveryBudgetRecord).where(
                RecoveryBudgetRecord.account_id == account_id,
                RecoveryBudgetRecord.intent_id == intent_id,
                RecoveryBudgetRecord.symbol == symbol,
            ).with_for_update()).scalar_one_or_none()
            if budget is None or not budget.approved or budget.attempts_used < 1:
                return False
            if Decimal(budget.notional_used) + notional > Decimal(budget.max_notional):
                return False
            budget.notional_used = Decimal(budget.notional_used) + notional
            budget.updated_at_ns = time.time_ns()
            control = self._control(db, account_id, intent_id)
            self._event(db, control, "EMERGENCY_CLOSE_BUDGET_APPROVED", {
                "symbol": symbol, "side": side, "quantity": quantity,
                "limit_price": str(limit_price), "notional": str(notional),
            })
            return True

    def alert_operator(self, *, account_id: str, intent_id: str, reason: str) -> None:
        self.append_event(account_id=account_id, intent_id=intent_id,
                          event="OPERATOR_ALERT", details={"reason": reason})
