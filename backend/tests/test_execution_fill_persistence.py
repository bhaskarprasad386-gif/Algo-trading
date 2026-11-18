from decimal import Decimal
import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.contracts import ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide
from app.execution.persistence import IdempotencyConflict, create_intent, record_broker_order, record_fill, transition_intent
from app.models.execution_intent import ExecutionBrokerOrderRecord, ExecutionFillRecord, ExecutionIntentEventRecord, ExecutionIntentRecord, ExecutionLegRecord


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
        schema = f"execution_fill_test_{uuid.uuid4().hex}"
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


def create_working_intent(db, *, working=True, register_orders=True):
    intent = ExecutionIntent("intent-1", "acct-1", "key-1", "corr-1", ExecutionMode.PAPER, created_at_ns=100)
    legs = tuple(
        ExecutionLeg(leg_id, symbol, side, 50, limit_price=price)
        for leg_id, symbol, side, price in (
            ("cb", "NFO:CE", OrderSide.BUY, 100),
            ("cs", "NFO:CE-HIGH", OrderSide.SELL, 80),
            ("pb", "NFO:PE", OrderSide.BUY, 90),
            ("ps", "NFO:PE-HIGH", OrderSide.SELL, 70),
        )
    )
    create_intent(db, intent, legs)
    state = IntentStatus.CREATED
    for target in (
        IntentStatus.VALIDATING, IntentStatus.RISK_CHECK, IntentStatus.APPROVED,
        IntentStatus.RESERVED, IntentStatus.SUBMITTING,
        *((IntentStatus.WORKING,) if working else ()),
    ):
        saved = transition_intent(
            db, account_id="acct-1", intent_id="intent-1",
            expected_version=0 if state is IntentStatus.CREATED else saved.version,
            target=target,
        )
        state = target
    if register_orders:
        for leg_id, symbol, side in (
            ("cb", "NFO:CE", "BUY"),
            ("cs", "NFO:CE-HIGH", "SELL"),
            ("pb", "NFO:PE", "BUY"),
            ("ps", "NFO:PE-HIGH", "SELL"),
        ):
            record_broker_order(
                db, account_id="acct-1", intent_id="intent-1", leg_id=leg_id,
                broker="angel_one", broker_order_id=f"order-{leg_id}",
                symbol=symbol, side=side, quantity=50, accepted_at_ns=2000,
                client_order_id=f"client-{leg_id}",
            )
    return saved


def fill(db, leg_id, fill_id, quantity=25, price="101.25"):
    return record_fill(
        db, account_id="acct-1", intent_id="intent-1", leg_id=leg_id,
        broker="Angel_One", broker_fill_id=fill_id, broker_order_id=f"order-{leg_id}",
        quantity=quantity, fill_price=Decimal(price), occurred_at_ns=1000 + len(fill_id),
    )


def test_first_fill_from_submitting_records_both_valid_transitions(db):
    saved = create_working_intent(db, working=False)
    assert saved.intent.status is IntentStatus.SUBMITTING
    result = fill(db, "cb", "trade-submitting", 10)
    events = db.scalars(
        select(ExecutionIntentEventRecord).where(
            ExecutionIntentEventRecord.sequence > saved.version
        ).order_by(ExecutionIntentEventRecord.sequence)
    ).all()
    assert result["intent_status"] == IntentStatus.PARTIALLY_FILLED.value
    assert [(event.from_status, event.to_status) for event in events] == [
        ("SUBMITTING", "WORKING"),
        ("WORKING", "PARTIALLY_FILLED"),
    ]


def test_fill_is_persisted_and_partial_progress_is_atomic(db):
    create_working_intent(db)
    result = fill(db, "cb", "trade-1", 20)
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    stored = db.scalar(select(ExecutionFillRecord))
    intent = db.scalar(select(ExecutionIntentRecord))
    assert result["created"] is True
    assert leg.filled_quantity == 20 and leg.status == "PARTIALLY_FILLED"
    assert stored.quantity == 20 and Decimal(stored.fill_price) == Decimal("101.25000000")
    assert intent.status == IntentStatus.PARTIALLY_FILLED.value


def test_duplicate_broker_fill_is_idempotent_and_not_double_counted(db):
    create_working_intent(db)
    first = fill(db, "cb", "trade-1", 20)
    duplicate = fill(db, "cb", "trade-1", 20)
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert first["created"] is True and duplicate["created"] is False
    assert leg.filled_quantity == 20
    assert db.query(ExecutionFillRecord).count() == 1


def test_reused_broker_fill_id_with_changed_payload_is_rejected(db):
    create_working_intent(db)
    fill(db, "cb", "trade-1", 20)
    with pytest.raises(IdempotencyConflict):
        record_fill(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_fill_id="trade-1", broker_order_id="order-cb",
            quantity=21, fill_price="101.25", occurred_at_ns=1008,
        )


def test_fill_cannot_overrun_requested_quantity(db):
    create_working_intent(db)
    fill(db, "cb", "trade-1", 40)
    with pytest.raises(ValueError, match="exceed"):
        fill(db, "cb", "trade-2", 11)
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert leg.filled_quantity == 40
    assert db.query(ExecutionFillRecord).count() == 1


def test_intent_becomes_filled_only_after_every_basket_leg_is_filled(db):
    create_working_intent(db)
    for i, leg_id in enumerate(("cb", "cs", "pb", "ps")):
        result = fill(db, leg_id, f"trade-{i}", 50)
    intent = db.scalar(select(ExecutionIntentRecord))
    assert result["intent_status"] == IntentStatus.FILLED.value
    assert intent.status == IntentStatus.FILLED.value
    events = db.scalars(select(ExecutionIntentEventRecord).order_by(ExecutionIntentEventRecord.sequence)).all()
    assert events[-1].to_status == IntentStatus.FILLED.value


def test_reconciliation_required_blocks_fill_mutation_until_review(db):
    saved = create_working_intent(db)
    transition_intent(
        db, account_id="acct-1", intent_id="intent-1",
        expected_version=saved.version, target=IntentStatus.UNKNOWN,
    )
    unknown = transition_intent(
        db, account_id="acct-1", intent_id="intent-1",
        expected_version=saved.version + 1, target=IntentStatus.RECONCILIATION_REQUIRED,
    )
    with pytest.raises(ValueError, match="reconciliation must resolve"):
        fill(db, "cb", "trade-1", 10)
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert leg.filled_quantity == 0



def test_sequential_partial_fill_updates_accumulate_and_replay_is_ignored(db):
    create_working_intent(db)
    first = fill(db, "cb", "trade-part-1", 10)
    second = fill(db, "cb", "trade-part-2", 15)
    replay = fill(db, "cb", "trade-part-1", 10)

    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert first["filled_quantity"] == 10
    assert second["filled_quantity"] == 25
    assert replay["created"] is False
    assert leg.filled_quantity == 25
    assert leg.status == "PARTIALLY_FILLED"
    assert db.query(ExecutionFillRecord).count() == 2


def _record_order(db, *, broker_order_id="order-cb", client_order_id="client-cb"):
    return record_broker_order(
        db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
        broker="Angel_One", broker_order_id=broker_order_id,
        symbol="NFO:CE", side="BUY", quantity=50, accepted_at_ns=2000,
        client_order_id=client_order_id,
    )


def test_duplicate_broker_order_update_identity_is_idempotent(db):
    create_working_intent(db)
    first = _record_order(db)
    duplicate = _record_order(db)

    assert first == {"created": False, "broker_order_id": "order-cb"}
    assert duplicate == {"created": False, "broker_order_id": "order-cb"}
    assert db.query(ExecutionBrokerOrderRecord).count() == 4


def test_broker_order_identity_reuse_with_changed_payload_is_rejected(db):
    create_working_intent(db)
    _record_order(db)
    with pytest.raises(IdempotencyConflict, match="reused"):
        _record_order(db, client_order_id="different-client-order")

    assert db.query(ExecutionBrokerOrderRecord).count() == 4


def test_fill_requires_registered_broker_order_for_same_account_intent_and_leg(db):
    create_working_intent(db)
    with pytest.raises(LookupError, match="broker order is not registered"):
        record_fill(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_fill_id="trade-unregistered",
            broker_order_id="unknown-order", quantity=10,
            fill_price="101.25", occurred_at_ns=3000,
        )
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert leg.filled_quantity == 0
    assert db.query(ExecutionFillRecord).count() == 0


def test_fill_cannot_be_attached_to_another_legs_broker_order(db):
    create_working_intent(db)
    with pytest.raises(LookupError, match="broker order is not registered"):
        record_fill(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cs",
            broker="angel_one", broker_fill_id="trade-wrong-leg",
            broker_order_id="order-cb", quantity=10,
            fill_price="101.25", occurred_at_ns=3001,
        )
    call_leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    put_leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cs"))
    assert call_leg.filled_quantity == put_leg.filled_quantity == 0
    assert db.query(ExecutionFillRecord).count() == 0



def test_second_distinct_broker_order_for_same_leg_is_rejected_until_cancel_replace_exists(db):
    create_working_intent(db)
    with pytest.raises(ValueError, match="cancel/replace is not supported"):
        record_broker_order(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_order_id="second-order-cb",
            symbol="NFO:CE", side="BUY", quantity=50, accepted_at_ns=3000,
        )
    assert db.query(ExecutionBrokerOrderRecord).count() == 4



@pytest.mark.parametrize("order_quantity", [49, 51])
def test_broker_order_quantity_must_exactly_match_execution_leg(db, order_quantity):
    create_working_intent(db, register_orders=False)

    with pytest.raises(ValueError, match="does not exactly match"):
        record_broker_order(
            db, account_id="acct-1", intent_id="intent-1", leg_id="cb",
            broker="angel_one", broker_order_id=f"wrong-quantity-{order_quantity}",
            symbol="NFO:CE", side="BUY", quantity=order_quantity,
            accepted_at_ns=3000,
        )

    assert db.query(ExecutionBrokerOrderRecord).count() == 0
