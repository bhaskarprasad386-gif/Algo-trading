import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.contracts import ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide
from app.execution.persistence import (
    ConcurrentTransitionError, IdempotencyConflict, create_intent, get_intent,
    record_broker_order, transition_intent,
)
from app.models.execution_intent import ExecutionIntentEventRecord, ExecutionIntentRecord, ExecutionLegRecord


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
        schema = f"execution_test_{uuid.uuid4().hex}"
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


def request(account="acct-1", key="key-1", intent_id="intent-1"):
    intent = ExecutionIntent(intent_id, account, key, "corr-1", ExecutionMode.PAPER, created_at_ns=100)
    legs = (
        ExecutionLeg("cb", "NFO:CE", OrderSide.BUY, 50, limit_price=100),
        ExecutionLeg("cs", "NFO:CE-HIGH", OrderSide.SELL, 50, limit_price=80),
        ExecutionLeg("pb", "NFO:PE", OrderSide.BUY, 50, limit_price=90),
        ExecutionLeg("ps", "NFO:PE-HIGH", OrderSide.SELL, 50, limit_price=70),
    )
    return intent, legs


def test_create_is_atomic_and_persists_four_legs_and_initial_event(db):
    intent, legs = request()
    saved, created = create_intent(db, intent, legs)
    assert created and saved.version == 0 and len(saved.legs) == 4
    assert db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "ps")).quantity == 50
    event = db.scalar(select(ExecutionIntentEventRecord))
    assert (event.sequence, event.from_status, event.to_status) == (0, None, "CREATED")


def test_idempotency_replays_same_payload_but_rejects_changed_payload(db):
    intent, legs = request()
    first, created = create_intent(db, intent, legs)
    replay, replay_created = create_intent(db, intent, legs)
    assert created and not replay_created and replay.intent.intent_id == first.intent.intent_id
    changed = list(legs)
    changed[0] = ExecutionLeg("cb", "NFO:OTHER", OrderSide.BUY, 50, limit_price=100)
    with pytest.raises(IdempotencyConflict):
        create_intent(db, intent, tuple(changed))


def test_idempotency_key_is_scoped_to_account(db):
    intent, legs = request("acct-1", intent_id="i-1")
    other, other_legs = request("acct-2", intent_id="i-2")
    create_intent(db, intent, legs)
    saved, created = create_intent(db, other, other_legs)
    assert created and saved.intent.account_id == "acct-2"
    assert db.query(ExecutionIntentRecord).count() == 2


def test_state_and_append_only_events_commit_together(db):
    intent, legs = request()
    create_intent(db, intent, legs)
    a = transition_intent(db, account_id="acct-1", intent_id="intent-1", expected_version=0, target=IntentStatus.VALIDATING, occurred_at_ns=200)
    b = transition_intent(db, account_id="acct-1", intent_id="intent-1", expected_version=1, target=IntentStatus.RISK_CHECK, occurred_at_ns=300, details={"risk":"passed"})
    assert (a.version, b.version, b.intent.status) == (1, 2, IntentStatus.RISK_CHECK)
    events = db.scalars(select(ExecutionIntentEventRecord).order_by(ExecutionIntentEventRecord.sequence)).all()
    assert [x.to_status for x in events] == ["CREATED", "VALIDATING", "RISK_CHECK"]


def test_stale_version_and_illegal_transition_are_rejected(db):
    intent, legs = request()
    create_intent(db, intent, legs)
    transition_intent(db, account_id="acct-1", intent_id="intent-1", expected_version=0, target=IntentStatus.VALIDATING)
    with pytest.raises(ConcurrentTransitionError):
        transition_intent(db, account_id="acct-1", intent_id="intent-1", expected_version=0, target=IntentStatus.RISK_CHECK)
    with pytest.raises(ValueError, match="invalid intent transition"):
        transition_intent(db, account_id="acct-1", intent_id="intent-1", expected_version=1, target=IntentStatus.FILLED)


def test_lookup_is_account_scoped_and_restart_state_reconciles(db):
    intent, legs = request()
    create_intent(db, intent, legs)
    for version, target in enumerate((IntentStatus.VALIDATING, IntentStatus.RISK_CHECK, IntentStatus.APPROVED, IntentStatus.RESERVED, IntentStatus.SUBMITTING, IntentStatus.UNKNOWN)):
        transition_intent(db, account_id="acct-1", intent_id="intent-1", expected_version=version, target=target)
    assert get_intent(db, account_id="acct-2", intent_id="intent-1") is None
    restored = get_intent(db, account_id="acct-1", intent_id="intent-1")
    assert restored.intent.status is IntentStatus.UNKNOWN
    recovered = transition_intent(db, account_id="acct-1", intent_id="intent-1", expected_version=6, target=IntentStatus.RECONCILIATION_REQUIRED)
    assert recovered.intent.status is IntentStatus.RECONCILIATION_REQUIRED
    with pytest.raises(ValueError, match="invalid intent transition"):
        transition_intent(db, account_id="acct-1", intent_id="intent-1", expected_version=7, target=IntentStatus.SUBMITTING)



def _submitting_intent(db):
    intent, legs = request()
    create_intent(db, intent, legs)
    for version, target in enumerate((
        IntentStatus.VALIDATING, IntentStatus.RISK_CHECK, IntentStatus.APPROVED,
        IntentStatus.RESERVED, IntentStatus.SUBMITTING,
    )):
        transition_intent(
            db, account_id="acct-1", intent_id="intent-1",
            expected_version=version, target=target,
        )


def test_broker_order_identity_is_durable_and_idempotent(db):
    _submitting_intent(db)
    first = record_broker_order(
        db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="ANGEL_ONE", broker_order_id="broker-ord-1",
        symbol="NFO:CE", side="BUY", quantity=50,
        accepted_at_ns=500,
    )
    replay = record_broker_order(
        db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="angel_one", broker_order_id="broker-ord-1",
        symbol="NFO:CE", side="BUY", quantity=50,
        broker_status="OPEN", accepted_at_ns=500,
    )
    assert first == {"created": True, "broker_order_id": "broker-ord-1"}
    assert replay == {"created": False, "broker_order_id": "broker-ord-1"}
    from app.models.execution_intent import ExecutionBrokerOrderRecord
    saved = db.scalar(select(ExecutionBrokerOrderRecord))
    assert (saved.account_id, saved.broker, saved.leg_record_id, saved.quantity) == (
        "acct-1", "angel_one", saved.leg_record_id, 50,
    )


def test_broker_order_id_reuse_with_different_payload_is_rejected(db):
    _submitting_intent(db)
    args = dict(
        account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="angel_one", broker_order_id="broker-ord-1",
        symbol="NFO:CE", side="BUY", quantity=50,
        broker_status="OPEN", accepted_at_ns=500,
    )
    record_broker_order(db, **args)
    with pytest.raises(IdempotencyConflict, match="broker order ID"):
        record_broker_order(db, **{**args, "quantity": 25})


def test_broker_order_identity_is_account_scoped_and_leg_validated(db):
    _submitting_intent(db)
    with pytest.raises(LookupError, match="account"):
        record_broker_order(
            db, account_id="acct-2", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_order_id="broker-ord-1",
            symbol="NFO:CE", side="BUY", quantity=50,
            broker_status="OPEN", accepted_at_ns=500,
        )
    with pytest.raises(ValueError, match="execution leg"):
        record_broker_order(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_order_id="broker-ord-2",
            symbol="NFO:CE", side="SELL", quantity=50,
            broker_status="OPEN", accepted_at_ns=500,
        )


def test_broker_order_identity_does_not_advance_intent_state(db):
    _submitting_intent(db)
    before = get_intent(db, account_id="acct-1", intent_id="intent-1")
    record_broker_order(
        db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="angel_one", broker_order_id="broker-ord-1",
        symbol="NFO:CE", side="BUY", quantity=50,
        broker_status="OPEN", accepted_at_ns=500,
    )
    after = get_intent(db, account_id="acct-1", intent_id="intent-1")
    assert after.intent.status == before.intent.status == IntentStatus.SUBMITTING
    assert after.version == before.version
