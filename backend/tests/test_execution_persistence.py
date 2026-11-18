import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.contracts import ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide
from app.execution.persistence import (
    ConcurrentTransitionError, IdempotencyConflict, create_intent, get_intent,
    record_broker_order, record_fill, transition_intent,
)
from app.models.execution_intent import ExecutionFillRecord, ExecutionIntentEventRecord, ExecutionIntentRecord, ExecutionLegRecord


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
        accepted_at_ns=501,
    )
    assert first == {"created": True, "broker_order_id": "broker-ord-1"}
    assert replay == {"created": False, "broker_order_id": "broker-ord-1"}
    from app.models.execution_intent import ExecutionBrokerOrderRecord
    saved = db.scalar(select(ExecutionBrokerOrderRecord))
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert (saved.account_id, saved.broker, saved.leg_record_id, saved.quantity) == (
        "acct-1", "angel_one", leg.id, 50,
    )


def test_broker_order_id_reuse_with_different_payload_is_rejected(db):
    _submitting_intent(db)
    args = dict(
        account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="angel_one", broker_order_id="broker-ord-1",
        symbol="NFO:CE", side="BUY", quantity=50,
        accepted_at_ns=500,
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
            accepted_at_ns=500,
        )
    with pytest.raises(ValueError, match="execution leg"):
        record_broker_order(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_order_id="broker-ord-2",
            symbol="NFO:CE", side="SELL", quantity=50,
            accepted_at_ns=500,
        )


def test_broker_order_identity_does_not_advance_intent_state(db):
    _submitting_intent(db)
    before = get_intent(db, account_id="acct-1", intent_id="intent-1")
    record_broker_order(
        db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="angel_one", broker_order_id="broker-ord-1",
        symbol="NFO:CE", side="BUY", quantity=50,
        accepted_at_ns=500,
    )
    after = get_intent(db, account_id="acct-1", intent_id="intent-1")
    assert after.intent.status == before.intent.status == IntentStatus.SUBMITTING
    assert after.version == before.version


def _register_first_leg_order(db, *, account_id="acct-1", intent_id="intent-1", broker="angel_one", order_id="broker-order-1"):
    record_broker_order(
        db, account_id=account_id, intent_id=intent_id, leg_id="cb",
        broker=broker, broker_order_id=order_id,
        symbol="NFO:CE", side="BUY", quantity=50,
        accepted_at_ns=500,
    )


def test_fill_from_wrong_broker_cannot_attach_to_registered_order(db):
    _submitting_intent(db)
    _register_first_leg_order(db, broker="angel_one", order_id="shared-order")

    with pytest.raises(LookupError, match="not registered"):
        record_fill(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
            broker="zerodha", broker_fill_id="wrong-broker-fill",
            broker_order_id="shared-order", quantity=10,
            fill_price="100.25", occurred_at_ns=600,
        )

    assert db.scalar(select(ExecutionFillRecord)) is None
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert leg.filled_quantity == 0


def test_fill_for_another_account_cannot_attach_to_existing_intent_or_order(db):
    _submitting_intent(db)
    _register_first_leg_order(db)

    with pytest.raises(LookupError, match="intent not found for account"):
        record_fill(
            db, account_id="acct-2", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_fill_id="cross-account-fill",
            broker_order_id="broker-order-1", quantity=10,
            fill_price="100.25", occurred_at_ns=600,
        )

    assert db.scalar(select(ExecutionFillRecord)) is None
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert leg.filled_quantity == 0


def test_fill_quantity_over_requested_leg_is_rejected_without_persistence(db):
    _submitting_intent(db)
    _register_first_leg_order(db)

    with pytest.raises(ValueError, match="exceed requested leg quantity"):
        record_fill(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_fill_id="overfill",
            broker_order_id="broker-order-1", quantity=51,
            fill_price="100.25", occurred_at_ns=600,
        )

    assert db.scalar(select(ExecutionFillRecord)) is None
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert leg.filled_quantity == 0
    assert leg.status == "PENDING"


def test_late_fill_after_restart_reconciliation_gate_rolls_back_all_progress(db):
    _submitting_intent(db)
    state = transition_intent(
        db, account_id="acct-1", intent_id="intent-1",
        expected_version=5, target=IntentStatus.UNKNOWN,
    )
    state = transition_intent(
        db, account_id="acct-1", intent_id="intent-1",
        expected_version=state.version, target=IntentStatus.RECONCILIATION_REQUIRED,
    )
    _register_first_leg_order(db)

    before_events = db.query(ExecutionIntentEventRecord).count()
    with pytest.raises(ValueError, match="reconciliation must resolve"):
        record_fill(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_fill_id="late-fill",
            broker_order_id="broker-order-1", quantity=10,
            fill_price="100.25", occurred_at_ns=700,
        )

    restored = get_intent(db, account_id="acct-1", intent_id="intent-1")
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert db.scalar(select(ExecutionFillRecord)) is None
    assert leg.filled_quantity == 0
    assert leg.status == "PENDING"
    assert restored.intent.status is IntentStatus.RECONCILIATION_REQUIRED
    assert restored.version == state.version
    assert db.query(ExecutionIntentEventRecord).count() == before_events


def test_broker_order_and_fill_ids_are_isolated_between_accounts(db):
    _submitting_intent(db)
    _register_first_leg_order(db, order_id="same-provider-order-id")

    other_intent, other_legs = request(account="acct-2", intent_id="intent-2")
    create_intent(db, other_intent, other_legs)
    for version, target in enumerate((
        IntentStatus.VALIDATING, IntentStatus.RISK_CHECK, IntentStatus.APPROVED,
        IntentStatus.RESERVED, IntentStatus.SUBMITTING,
    )):
        transition_intent(
            db, account_id="acct-2", intent_id="intent-2",
            expected_version=version, target=target,
        )
    _register_first_leg_order(
        db, account_id="acct-2", intent_id="intent-2", order_id="same-provider-order-id",
    )

    for account in ("acct-1", "acct-2"):
        result = record_fill(
            db, account_id=account, intent_id=("intent-1" if account == "acct-1" else "intent-2"), leg_id="cb",
            broker="angel_one", broker_fill_id="same-provider-fill-id",
            broker_order_id="same-provider-order-id", quantity=10,
            fill_price="100.25", occurred_at_ns=800,
        )
        assert result["created"] is True

    fills = db.scalars(select(ExecutionFillRecord).order_by(ExecutionFillRecord.account_id)).all()
    assert [(row.account_id, row.broker_fill_id, row.quantity) for row in fills] == [
        ("acct-1", "same-provider-fill-id", 10),
        ("acct-2", "same-provider-fill-id", 10),
    ]
    for account in ("acct-1", "acct-2"):
        leg = db.scalar(select(ExecutionLegRecord).join(
            ExecutionIntentRecord,
            ExecutionIntentRecord.id == ExecutionLegRecord.intent_record_id,
        ).where(
            ExecutionIntentRecord.account_id == account,
            ExecutionLegRecord.leg_id == "cb",
        ))
        assert leg.filled_quantity == 10



def test_fill_replay_is_idempotent_and_conflicting_reuse_is_rejected(db):
    _submitting_intent(db)
    _register_first_leg_order(db)

    args = dict(
        account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="ANGEL_ONE", broker_fill_id="provider-fill-1",
        broker_order_id="broker-order-1", quantity=10,
        fill_price="100.250", occurred_at_ns=900,
    )
    first = record_fill(db, **args)
    replay = record_fill(db, **{**args, "broker": "angel_one", "fill_price": "100.25"})

    assert first["created"] is True
    assert replay == {
        "created": False,
        "filled_quantity": None,
        "intent_status": None,
    }
    assert db.query(ExecutionFillRecord).count() == 1
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert leg.filled_quantity == 10

    with pytest.raises(IdempotencyConflict, match="broker fill ID"):
        record_fill(db, **{**args, "fill_price": "100.26"})

    assert db.query(ExecutionFillRecord).count() == 1
    assert leg.filled_quantity == 10


def test_four_leg_fill_completion_is_atomic_and_reaches_filled_only_after_last_leg(db):
    _submitting_intent(db)
    legs = (
        ("cb", "NFO:CE", "BUY"),
        ("cs", "NFO:CE-HIGH", "SELL"),
        ("pb", "NFO:PE", "BUY"),
        ("ps", "NFO:PE-HIGH", "SELL"),
    )
    for index, (leg_id, symbol, side) in enumerate(legs, start=1):
        record_broker_order(
            db, account_id="acct-1", intent_id="intent-1", leg_id=leg_id,
            broker="angel_one", broker_order_id=f"four-leg-order-{index}",
            symbol=symbol, side=side, quantity=50,
            accepted_at_ns=1000 + index,
        )

    for index, (leg_id, _symbol, _side) in enumerate(legs, start=1):
        result = record_fill(
            db, account_id="acct-1", intent_id="intent-1", leg_id=leg_id,
            broker="angel_one", broker_fill_id=f"four-leg-fill-{index}",
            broker_order_id=f"four-leg-order-{index}", quantity=50,
            fill_price="100.00", occurred_at_ns=1100 + index,
        )
        if index < len(legs):
            assert result["intent_status"] == IntentStatus.PARTIALLY_FILLED.value
        else:
            assert result["intent_status"] == IntentStatus.FILLED.value

    restored = get_intent(db, account_id="acct-1", intent_id="intent-1")
    assert restored.intent.status is IntentStatus.FILLED
    assert all(leg.quantity == leg.filled_quantity == 50 for leg in restored.legs)
    assert db.query(ExecutionFillRecord).count() == 4
    events = db.scalars(
        select(ExecutionIntentEventRecord).order_by(ExecutionIntentEventRecord.sequence)
    ).all()
    assert [event.to_status for event in events][-2:] == [
        IntentStatus.PARTIALLY_FILLED.value,
        IntentStatus.FILLED.value,
    ]


def test_partial_fill_callbacks_accumulate_per_leg_before_four_leg_completion(db):
    _submitting_intent(db)
    legs = (
        ("cb", "NFO:CE", "BUY"),
        ("cs", "NFO:CE-HIGH", "SELL"),
        ("pb", "NFO:PE", "BUY"),
        ("ps", "NFO:PE-HIGH", "SELL"),
    )
    for index, (leg_id, symbol, side) in enumerate(legs, start=1):
        record_broker_order(
            db, account_id="acct-1", intent_id="intent-1", leg_id=leg_id,
            broker="angel_one", broker_order_id=f"partial-order-{index}",
            symbol=symbol, side=side, quantity=50,
            accepted_at_ns=1200 + index,
        )

    first = record_fill(
        db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="angel_one", broker_fill_id="partial-fill-a",
        broker_order_id="partial-order-1", quantity=20,
        fill_price="100.10", occurred_at_ns=1301,
    )
    assert first["filled_quantity"] == 20
    assert first["leg_status"] == "PARTIALLY_FILLED"
    assert first["intent_status"] == IntentStatus.PARTIALLY_FILLED.value

    second = record_fill(
        db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="angel_one", broker_fill_id="partial-fill-b",
        broker_order_id="partial-order-1", quantity=30,
        fill_price="100.20", occurred_at_ns=1302,
    )
    assert second["filled_quantity"] == 50
    assert second["leg_status"] == "FILLED"
    assert second["intent_status"] == IntentStatus.PARTIALLY_FILLED.value

    for index, (leg_id, _symbol, _side) in enumerate(legs[1:], start=2):
        result = record_fill(
            db, account_id="acct-1", intent_id="intent-1", leg_id=leg_id,
            broker="angel_one", broker_fill_id=f"partial-fill-{index}",
            broker_order_id=f"partial-order-{index}", quantity=50,
            fill_price="80.00", occurred_at_ns=1300 + index,
        )
        if index < len(legs):
            assert result["intent_status"] == IntentStatus.PARTIALLY_FILLED.value
        else:
            assert result["intent_status"] == IntentStatus.FILLED.value

    restored = get_intent(db, account_id="acct-1", intent_id="intent-1")
    assert restored.intent.status is IntentStatus.FILLED
    assert all(leg.filled_quantity == leg.quantity == 50 for leg in restored.legs)
    assert db.query(ExecutionFillRecord).count() == 5


def test_cumulative_partial_fill_overrun_rolls_back_only_the_rejected_callback(db):
    _submitting_intent(db)
    _register_first_leg_order(db)

    accepted = record_fill(
        db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="angel_one", broker_fill_id="cumulative-fill-1",
        broker_order_id="broker-order-1", quantity=30,
        fill_price="100.10", occurred_at_ns=1401,
    )
    assert accepted["filled_quantity"] == 30
    assert accepted["intent_status"] == IntentStatus.PARTIALLY_FILLED.value
    before_events = db.query(ExecutionIntentEventRecord).count()

    with pytest.raises(ValueError, match="exceed requested leg quantity"):
        record_fill(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_fill_id="cumulative-fill-2",
            broker_order_id="broker-order-1", quantity=21,
            fill_price="100.20", occurred_at_ns=1402,
        )

    restored = get_intent(db, account_id="acct-1", intent_id="intent-1")
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    fills = db.scalars(select(ExecutionFillRecord)).all()
    assert len(fills) == 1
    assert fills[0].broker_fill_id == "cumulative-fill-1"
    assert leg.filled_quantity == 30
    assert leg.status == "PARTIALLY_FILLED"
    assert restored.intent.status is IntentStatus.PARTIALLY_FILLED
    assert restored.version == accepted["version"]
    assert db.query(ExecutionIntentEventRecord).count() == before_events
