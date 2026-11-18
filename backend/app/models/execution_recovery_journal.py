from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RecoveryControlRecord(Base):
    __tablename__ = "execution_recovery_controls"
    __table_args__ = (UniqueConstraint("account_id", "intent_id", name="uq_recovery_control_account_intent"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    intent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="RECOVERING")
    frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RecoveryKillSwitchRecord(Base):
    __tablename__ = "execution_recovery_kill_switches"
    __table_args__ = (UniqueConstraint("account_id", "strategy", name="uq_recovery_kill_account_strategy"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    latched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    latched_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RecoveryInstrumentLeaseRecord(Base):
    __tablename__ = "execution_recovery_instrument_leases"
    __table_args__ = (
        UniqueConstraint("account_id", "symbol", name="uq_recovery_lease_account_symbol"),
        Index("ix_recovery_lease_intent", "account_id", "intent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    intent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    acquired_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RecoveryBudgetRecord(Base):
    __tablename__ = "execution_recovery_budgets"
    __table_args__ = (UniqueConstraint("account_id", "intent_id", "symbol", name="uq_recovery_budget_scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    intent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_notional: Mapped[float] = mapped_column(Numeric(24, 8), nullable=False)
    notional_used: Mapped[float] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    updated_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RecoveryJournalEventRecord(Base):
    __tablename__ = "execution_recovery_journal_events"
    __table_args__ = (
        UniqueConstraint("control_id", "sequence", name="uq_recovery_event_sequence"),
        Index("ix_recovery_event_time", "control_id", "occurred_at_ns"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    control_id: Mapped[int] = mapped_column(ForeignKey("execution_recovery_controls.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    details_json: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
