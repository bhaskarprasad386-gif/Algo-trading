import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.contracts import ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide
from app.execution.persistence import create_intent, transition_intent
from app.execution.recovery import list_recovery_candidates
from app.models.execution_intent import ExecutionIntentRecord


@pytest.fixture(params=["sqlite", "postgres"])
def db(request):
    admin_engine = None
    schema = None
    if request.param == "sqlite":
        engine = create_engine("sqlite:///:memory:")
    else:
        url = os.getenv("IV_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("IV_TEST_POSTGRES_URL is not configured")
        admin_engine = create_engine(url, pool_pre_ping=True)
        schema = f"execution_recovery_test_{uuid.uuid4().hex}"
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            url, connect_args={"options": f"-csearch_path={schema}"}, pool_pre_ping=True
        )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        if admin_engine is not None:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            admin_engine.dispose()


def _create(db, *, account="acct-1", intent_id="intent-1"):
    intent = ExecutionIntent(intent_id, account, f"key-{intent_id}", "corr-1", ExecutionMode.PAPER, created_at_ns=100)
    legs = (
        ExecutionLeg("cb", "NFO:CE", OrderSide.BUY, 50, limit_price=100),
        ExecutionLeg("cs", "NFO:CE-HIGH", OrderSide.SELL, 50, limit_price=80),
        ExecutionLeg("pb", "NFO:PE", OrderSide.BUY, 50, limit_price=90),
        ExecutionLeg("ps", "NFO:PE-HIGH", OrderSide.SELL, 50, limit_price=70),
    )
    saved, _ = create_intent(db, intent, legs)
    return saved


def test_recovery_inventory_is_account_scoped_and_read_only(db):
    first = _create(db, account="acct-1", intent_id="first")
    second = _create(db, account="acct-2", intent_id="second")
    transition_intent(
        db, account_id="acct-1", intent_id="first",
        expected_version=first.version, target=IntentStatus.VALIDATING,
    )
    before = db.scalar(select(ExecutionIntentRecord).where(ExecutionIntentRecord.intent_id == "first")).version

    candidates = list_recovery_candidates(db, account_id="acct-1")

    assert [x.persisted.intent.intent_id for x in candidates] == ["first"]
    assert candidates[0].requires_broker_reconciliation is False
    assert len(candidates[0].leg_progress) == 4
    after = db.scalar(select(ExecutionIntentRecord).where(ExecutionIntentRecord.intent_id == "first")).version
    assert before == after == candidates[0].persisted.version


def test_unknown_and_working_intents_are_flagged_for_reconciliation(db):
    saved = _create(db)
    for version, target in enumerate((
        IntentStatus.VALIDATING, IntentStatus.RISK_CHECK, IntentStatus.APPROVED,
        IntentStatus.RESERVED, IntentStatus.SUBMITTING, IntentStatus.UNKNOWN,
    )):
        transition_intent(
            db, account_id="acct-1", intent_id="intent-1",
            expected_version=version, target=target,
        )

    candidates = list_recovery_candidates(db, account_id="acct-1")

    assert len(candidates) == 1
    assert candidates[0].persisted.intent.status is IntentStatus.UNKNOWN
    assert candidates[0].requires_broker_reconciliation is True


def test_terminal_intents_are_not_in_recovery_inventory(db):
    saved = _create(db)
    transition_intent(
        db, account_id="acct-1", intent_id="intent-1",
        expected_version=saved.version, target=IntentStatus.VALIDATING,
    )
    transition_intent(
        db, account_id="acct-1", intent_id="intent-1",
        expected_version=1, target=IntentStatus.REJECTED,
    )
    assert list_recovery_candidates(db, account_id="acct-1") == ()


def test_recovery_inventory_requires_account_and_bounded_limit(db):
    with pytest.raises(ValueError, match="account_id"):
        list_recovery_candidates(db, account_id=" ")
    with pytest.raises(ValueError, match="limit"):
        list_recovery_candidates(db, account_id="acct-1", limit=0)
