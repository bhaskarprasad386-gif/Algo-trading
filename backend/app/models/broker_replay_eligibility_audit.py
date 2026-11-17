"""Append-only audit record for operator replay-eligibility reviews."""
from __future__ import annotations

from sqlalchemy import BigInteger, DDL, Index, Integer, String, Text, event
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


# Metadata after_create runs on every create_all(), including when this table
# already exists. That makes the guards self-healing on application startup
# after upgrades, rather than protecting only a freshly-created table.
event.listen(
    Base.metadata,
    "after_create",
    DDL("""
        CREATE TRIGGER IF NOT EXISTS trg_replay_eligibility_audit_no_update
        BEFORE UPDATE ON broker_replay_eligibility_audits
        BEGIN
            SELECT RAISE(ABORT, 'broker replay eligibility audits are append-only');
        END
    """).execute_if(dialect="sqlite"),
)
event.listen(
    Base.metadata,
    "after_create",
    DDL("""
        CREATE TRIGGER IF NOT EXISTS trg_replay_eligibility_audit_no_delete
        BEFORE DELETE ON broker_replay_eligibility_audits
        BEGIN
            SELECT RAISE(ABORT, 'broker replay eligibility audits are append-only');
        END
    """).execute_if(dialect="sqlite"),
)
event.listen(
    Base.metadata,
    "after_create",
    DDL("""
        CREATE OR REPLACE FUNCTION broker_replay_eligibility_audit_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'broker replay eligibility audits are append-only';
        END;
        $$
    """).execute_if(dialect="postgresql"),
)
event.listen(
    Base.metadata,
    "after_create",
    DDL("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_replay_eligibility_audit_no_update_delete'
                  AND tgrelid = 'broker_replay_eligibility_audits'::regclass
                  AND NOT tgisinternal
            ) THEN
                CREATE TRIGGER trg_replay_eligibility_audit_no_update_delete
                BEFORE UPDATE OR DELETE ON broker_replay_eligibility_audits
                FOR EACH ROW EXECUTE FUNCTION broker_replay_eligibility_audit_reject_mutation();
            END IF;
        END;
        $$
    """).execute_if(dialect="postgresql"),
)
