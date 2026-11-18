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
            if row.state == RecoveryState.MANUAL_REVIEW.value and state is RecoveryState.RECOVERING:
                raise ValueError("manual review cannot resume automated unwind without a new approved intent")
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
        self._identity(account_id, intent_id)
        if not symbol.strip():
            raise ValueError("recovery symbol is required")
        with self._session_factory() as db, db.begin():
            # Lock order is control -> lease -> budget for both claim and approval.
            # This serializes workers and avoids consuming attempts before recovery
            # has an active frozen intent and exclusive ownership of the instrument.
            control = db.execute(select(RecoveryControlRecord).where(
                RecoveryControlRecord.account_id == account_id,
                RecoveryControlRecord.intent_id == intent_id,
            ).with_for_update()).scalar_one_or_none()
            if (control is None or not control.frozen or not control.risk_reserved
                or control.state != RecoveryState.RECOVERING.value):
                raise RuntimeError("frozen risk-reserved EMERGENCY_UNWIND intent is required")
            lease = db.execute(select(RecoveryInstrumentLeaseRecord).where(
                RecoveryInstrumentLeaseRecord.account_id == account_id,
                RecoveryInstrumentLeaseRecord.intent_id == intent_id,
                RecoveryInstrumentLeaseRecord.symbol == symbol,
            ).with_for_update()).scalar_one_or_none()
            if lease is None:
                raise RuntimeError("exclusive instrument lease is required")
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
            self._event(db, control, "RECOVERY_ATTEMPT_CLAIMED", {
                "symbol": symbol, "attempt": budget.attempts_used,
            })
            return budget.attempts_used

    def approve_emergency_close(self, *, account_id: str, intent_id: str,
                                symbol: str, side: str, quantity: int,
                                limit_price: Decimal, notional: Decimal,
                                client_order_id: str) -> bool:
        if (not client_order_id.strip() or side not in {"BUY", "SELL"}
            or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0):
            return False
        if not limit_price.is_finite() or limit_price <= 0 or not notional.is_finite() or notional <= 0:
            return False
        expected_notional = limit_price * quantity
        if notional != expected_notional:
            return False
        with self._session_factory() as db, db.begin():
            control = db.execute(select(RecoveryControlRecord).where(
                RecoveryControlRecord.account_id == account_id,
                RecoveryControlRecord.intent_id == intent_id,
            ).with_for_update()).scalar_one_or_none()
            if (control is None or not control.frozen or not control.risk_reserved
                or control.state != RecoveryState.RECOVERING.value):
                return False
            lease = db.execute(select(RecoveryInstrumentLeaseRecord).where(
                RecoveryInstrumentLeaseRecord.account_id == account_id,
                RecoveryInstrumentLeaseRecord.intent_id == intent_id,
                RecoveryInstrumentLeaseRecord.symbol == symbol,
            ).with_for_update()).scalar_one_or_none()
            if lease is None:
                return False
            budget = db.execute(select(RecoveryBudgetRecord).where(
                RecoveryBudgetRecord.account_id == account_id,
                RecoveryBudgetRecord.intent_id == intent_id,
                RecoveryBudgetRecord.symbol == symbol,
            ).with_for_update()).scalar_one_or_none()
            if budget is None or not budget.approved or budget.attempts_used < 1:
                return False
            # A claimed attempt may authorize at most one close request. The
            # control row lock serializes approvals; the append-only journal is
            # the durable consumed-attempt ledger, avoiding a second counter.
            prior_approvals = db.execute(select(RecoveryJournalEventRecord).where(
                RecoveryJournalEventRecord.control_id == control.id,
                RecoveryJournalEventRecord.event == "EMERGENCY_CLOSE_BUDGET_APPROVED",
            )).scalars().all()
            approvals_for_symbol = sum(
                1 for event in prior_approvals
                if json.loads(event.details_json).get("symbol") == symbol
            )
            if approvals_for_symbol >= budget.attempts_used:
                return False
            if Decimal(budget.notional_used) + notional > Decimal(budget.max_notional):
                return False
            budget.notional_used = Decimal(budget.notional_used) + notional
            budget.updated_at_ns = time.time_ns()
            self._event(db, control, "EMERGENCY_CLOSE_BUDGET_APPROVED", {
                "symbol": symbol, "side": side, "quantity": quantity,
                "limit_price": str(limit_price), "notional": str(notional),
                "client_order_id": client_order_id,
            })
            return True


    def pending_close_authorizations(self, *, account_id: str, intent_id: str) -> tuple[str, ...]:
        """Return approved close IDs not yet reconciled against an authoritative broker snapshot."""
        self._identity(account_id, intent_id)
        with self._session_factory() as db:
            control = db.execute(select(RecoveryControlRecord).where(
                RecoveryControlRecord.account_id == account_id,
                RecoveryControlRecord.intent_id == intent_id,
            )).scalar_one_or_none()
            if control is None:
                return ()
            events = db.execute(select(RecoveryJournalEventRecord).where(
                RecoveryJournalEventRecord.control_id == control.id,
            ).order_by(RecoveryJournalEventRecord.sequence)).scalars().all()
            approved = []
            reconciled = set()
            for event in events:
                details = json.loads(event.details_json)
                if event.event == "EMERGENCY_CLOSE_BUDGET_APPROVED":
                    client_id = details.get("client_order_id")
                    if client_id:
                        approved.append(client_id)
                elif event.event == "EMERGENCY_CLOSE_RECONCILED":
                    client_id = details.get("client_order_id")
                    if client_id:
                        reconciled.add(client_id)
            return tuple(client_id for client_id in approved if client_id not in reconciled)

    def mark_close_authorization_reconciled(self, *, account_id: str, intent_id: str,
                                            client_order_id: str) -> None:
        self._identity(account_id, intent_id)
        if not client_order_id.strip():
            raise ValueError("client order identity is required")
        with self._session_factory() as db, db.begin():
            control = db.execute(select(RecoveryControlRecord).where(
                RecoveryControlRecord.account_id == account_id,
                RecoveryControlRecord.intent_id == intent_id,
            ).with_for_update()).scalar_one_or_none()
            if control is None:
                raise LookupError("recovery control does not exist")
            pending = db.execute(select(RecoveryJournalEventRecord).where(
                RecoveryJournalEventRecord.control_id == control.id,
                RecoveryJournalEventRecord.event == "EMERGENCY_CLOSE_RECONCILED",
            )).scalars().all()
            if any(json.loads(event.details_json).get("client_order_id") == client_order_id for event in pending):
                return
            self._event(db, control, "EMERGENCY_CLOSE_RECONCILED", {
                "client_order_id": client_order_id,
            })

    def alert_operator(self, *, account_id: str, intent_id: str, reason: str) -> None:
        self.append_event(account_id=account_id, intent_id=intent_id,
                          event="OPERATOR_ALERT", details={"reason": reason})
