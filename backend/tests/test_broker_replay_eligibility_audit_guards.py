import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.broker_replay_eligibility_audit import BrokerReplayEligibilityAuditRecord


@pytest.fixture
def audit_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[BrokerReplayEligibilityAuditRecord.__table__])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def audit_session(audit_engine):
    session = sessionmaker(bind=audit_engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


def _insert_audit(session):
    record = BrokerReplayEligibilityAuditRecord(
        review_id="review-immutable",
        broker="fyers",
        account_id="acct-1",
        event_ids_json='["evt-1"]',
        evidence_digest="a" * 64,
        result_status="BLOCKED",
        eligible=False,
        reviewed_at_ns=1_000,
        valid_until_ns=61_000,
    )
    session.add(record)
    session.commit()
    return record.id


def test_audit_receipt_allows_insert(audit_session):
    _insert_audit(audit_session)
    assert audit_session.query(BrokerReplayEligibilityAuditRecord).count() == 1


def test_audit_receipt_rejects_orm_update(audit_session):
    record_id = _insert_audit(audit_session)
    record = audit_session.get(BrokerReplayEligibilityAuditRecord, record_id)
    record.result_status = "ELIGIBLE"
    with pytest.raises(DBAPIError, match="append-only"):
        audit_session.commit()
    audit_session.rollback()


def test_audit_receipt_rejects_bulk_delete_and_direct_sql(audit_session):
    _insert_audit(audit_session)
    with pytest.raises(DBAPIError, match="append-only"):
        audit_session.query(BrokerReplayEligibilityAuditRecord).filter_by(
            review_id="review-immutable",
        ).delete(synchronize_session=False)
        audit_session.commit()
    audit_session.rollback()
    assert audit_session.query(BrokerReplayEligibilityAuditRecord).count() == 1


def test_create_all_repairs_missing_guards_on_existing_table(audit_engine, audit_session):
    _insert_audit(audit_session)
    with audit_engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER trg_replay_eligibility_audit_no_update")
        connection.exec_driver_sql("DROP TRIGGER trg_replay_eligibility_audit_no_delete")

    # Simulate an upgraded database where the audit table predates this guard
    # installation (or where a trigger was accidentally removed).
    Base.metadata.create_all(
        audit_engine,
        tables=[BrokerReplayEligibilityAuditRecord.__table__],
    )

    record = audit_session.query(BrokerReplayEligibilityAuditRecord).one()
    record.result_status = "MUTATED"
    with pytest.raises(DBAPIError, match="append-only"):
        audit_session.commit()
    audit_session.rollback()
