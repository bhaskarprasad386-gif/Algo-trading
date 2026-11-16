"""Append-only audit record for operator replay-eligibility reviews."""
from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BrokerReplayEligibilityAuditRecord(Base):
    __tablename__ = "broker_replay_eligibility_audits"
    __table_args__ = (
        Index("ix_replay_eligibility_audit_account_time", "broker", "account_id", "reviewed_at_ns"),
        Index("ix_replay_eligibility_audit_review_id", "review_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[str] = mapped_column(String(64), nullable=False)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_status: Mapped[str] = mapped_column(String(48), nullable=False)
    eligible: Mapped[bool] = mapped_column(nullable=False, default=False)
    reviewed_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    valid_until_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operator_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="box-spread-real-owner")
