from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BasketExecutionJournalRecord(Base):
    """Durable coordinator claim and latest state for one account-scoped basket."""

    __tablename__ = "basket_execution_journals"
    __table_args__ = (
        UniqueConstraint("account_id", "intent_id", name="uq_basket_journal_account_intent"),
        Index("ix_basket_journal_account_state", "account_id", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    intent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(192), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    client_order_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reconciliation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)


class BasketExecutionJournalEventRecord(Base):
    """Append-only event history for coordinator decisions and broker receipts."""

    __tablename__ = "basket_execution_journal_events"
    __table_args__ = (
        UniqueConstraint("journal_id", "sequence", name="uq_basket_journal_event_sequence"),
        Index("ix_basket_journal_event_time", "journal_id", "occurred_at_ns"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    journal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
