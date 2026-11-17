"""Append-only audit record for operator replay-eligibility reviews."""
from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, String, Text, event, inspect
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


def _install_replay_eligibility_audit_guards(target, connection, **_kw) -> None:
    """Install guards only when the audit table exists on this database.

    Metadata after_create also fires for create_all() calls that create only a
    subset of tables. Checking table presence prevents unrelated isolated test
    schemas from failing while still repairing guards on existing deployments.
    """
    table_name = BrokerReplayEligibilityAuditRecord.__tablename__
    if not inspect(connection).has_table(table_name):
        return

    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS trg_replay_eligibility_audit_no_update
            BEFORE UPDATE ON broker_replay_eligibility_audits
            BEGIN
                SELECT RAISE(ABORT, 'broker replay eligibility audits are append-only');
            END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS trg_replay_eligibility_audit_no_delete
            BEFORE DELETE ON broker_replay_eligibility_audits
            BEGIN
                SELECT RAISE(ABORT, 'broker replay eligibility audits are append-only');
            END
        """)
    elif dialect == "postgresql":
        connection.exec_driver_sql("""
            CREATE OR REPLACE FUNCTION broker_replay_eligibility_audit_reject_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'broker replay eligibility audits are append-only';
            END;
            $$
        """)
        trigger_exists = connection.exec_driver_sql("""
            SELECT EXISTS (
                SELECT 1
                FROM pg_trigger
                WHERE tgname = 'trg_replay_eligibility_audit_no_update_delete'
                  AND tgrelid = to_regclass('broker_replay_eligibility_audits')
                  AND NOT tgisinternal
            )
        """).scalar()
        if not trigger_exists:
            connection.exec_driver_sql("""
                CREATE TRIGGER trg_replay_eligibility_audit_no_update_delete
                BEFORE UPDATE OR DELETE ON broker_replay_eligibility_audits
                FOR EACH ROW EXECUTE FUNCTION broker_replay_eligibility_audit_reject_mutation()
            """)


# Runs after normal metadata creation and on later create_all() startup calls.
event.listen(Base.metadata, "after_create", _install_replay_eligibility_audit_guards)
