"""Durable account-scoped evidence from read-only broker reconciliation."""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BrokerReconciliationRecord(Base):
    __tablename__ = "broker_reconciliation_reports"
    __table_args__ = (
        UniqueConstraint(
            "broker", "account_id", "reconciliation_id",
            name="uq_broker_reconciliation_identity",
        ),
        Index(
            "ix_broker_reconciliation_account_time",
            "broker", "account_id", "created_at_ns",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reconciliation_id: Mapped[str] = mapped_column(String(192), nullable=False)
    snapshot_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_consistent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    matched_order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_json: Mapped[str] = mapped_column(Text, nullable=False)
