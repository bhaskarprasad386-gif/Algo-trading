import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.broker_events import BrokerOrderEvent, BrokerOrderEventStatus
from app.execution.broker_event_persistence import BrokerEventConflict, record_broker_order_event
from app.models.broker_order_event import BrokerOrderEventRecord


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def event(**changes):
    values = dict(
        broker="fyers", account_id="acct-1", event_id="evt-1",
        broker_order_id="order-1", status=BrokerOrderEventStatus.PARTIALLY_FILLED,
        cumulative_filled_quantity=10, fill_id="trade-1", fill_quantity=10,
        average_fill_price=101.25, occurred_at_ns=123456,
    )
    values.update(changes)
    return BrokerOrderEvent(**values)


def test_persists_normalized_broker_event(db):
    result = record_broker_order_event(db, event())
    stored = db.scalar(select(BrokerOrderEventRecord))
    assert result["created"] is True
    assert stored.status == "PARTIALLY_FILLED"
    assert stored.account_id == "acct-1"
    assert stored.payload_hash


def test_exact_event_replay_is_idempotent(db):
    first = record_broker_order_event(db, event())
    duplicate = record_broker_order_event(db, event())
    assert first["created"] is True
    assert duplicate["created"] is False
    assert db.query(BrokerOrderEventRecord).count() == 1


def test_changed_payload_under_same_event_identity_is_rejected(db):
    record_broker_order_event(db, event())
    with pytest.raises(BrokerEventConflict, match="different payload"):
        record_broker_order_event(
            db, event(cumulative_filled_quantity=11, fill_quantity=11)
        )


def test_same_event_id_is_allowed_for_different_account(db):
    record_broker_order_event(db, event())
    other = record_broker_order_event(db, event(account_id="acct-2"))
    assert other["created"] is True
    assert db.query(BrokerOrderEventRecord).count() == 2


def test_event_store_does_not_mutate_execution_fill_or_order_state(db):
    record_broker_order_event(db, event())
    assert db.query(BrokerOrderEventRecord).count() == 1


@pytest.mark.parametrize("bad_price", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0])
def test_non_finite_or_non_positive_event_fill_price_is_rejected(bad_price):
    with pytest.raises(ValueError, match="finite and positive"):
        event(average_fill_price=bad_price)


from app.execution.contracts import (
    ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide,
)
from app.execution.persistence import create_intent, record_broker_order, transition_intent
from app.execution.broker_event_persistence import record_broker_order_event_with_fill
from app.models.execution_intent import ExecutionFillRecord, ExecutionIntentRecord, ExecutionLegRecord


def _working_intent_with_broker_order(db):
    intent = ExecutionIntent(
        "intent-atomic", "acct-1", "key-atomic", "corr-atomic",
        ExecutionMode.PAPER, created_at_ns=100,
    )
    legs = (
        ExecutionLeg("cb", "NFO:CE", OrderSide.BUY, 50, limit_price=100),
        ExecutionLeg("cs", "NFO:CE-HIGH", OrderSide.SELL, 50, limit_price=80),
        ExecutionLeg("pb", "NFO:PE", OrderSide.BUY, 50, limit_price=90),
        ExecutionLeg("ps", "NFO:PE-HIGH", OrderSide.SELL, 50, limit_price=70),
    )
    saved, _ = create_intent(db, intent, legs)
    version = saved.version
    for target in (
        IntentStatus.VALIDATING, IntentStatus.RISK_CHECK, IntentStatus.APPROVED,
        IntentStatus.RESERVED, IntentStatus.SUBMITTING,
    ):
        saved = transition_intent(
            db, account_id="acct-1", intent_id="intent-atomic",
            expected_version=version, target=target,
        )
        version = saved.version
    record_broker_order(
        db, account_id="acct-1", intent_id="intent-atomic", leg_id="cb",
        broker="fyers", broker_order_id="order-atomic", symbol="NFO:CE",
        side="BUY", quantity=50, accepted_at_ns=200,
    )


def _fill_event(**changes):
    values = dict(
        broker="fyers", account_id="acct-1", event_id="evt-fill-atomic",
        broker_order_id="order-atomic",
        status=BrokerOrderEventStatus.PARTIALLY_FILLED,
        cumulative_filled_quantity=10, fill_id="trade-atomic",
        fill_quantity=10, average_fill_price=101.25, occurred_at_ns=300,
    )
    values.update(changes)
    return BrokerOrderEvent(**values)


def test_event_and_fill_progress_commit_atomically(db):
    _working_intent_with_broker_order(db)
    result = record_broker_order_event_with_fill(db, _fill_event())
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    intent = db.scalar(select(ExecutionIntentRecord).where(
        ExecutionIntentRecord.intent_id == "intent-atomic"
    ))
    assert result["event_created"] is True
    assert result["fill_created"] is True
    assert db.query(BrokerOrderEventRecord).count() == 1
    assert db.query(ExecutionFillRecord).count() == 1
    assert leg.filled_quantity == 10
    assert intent.status == IntentStatus.PARTIALLY_FILLED.value


def test_duplicate_event_does_not_apply_fill_twice(db):
    _working_intent_with_broker_order(db)
    first = record_broker_order_event_with_fill(db, _fill_event())
    replay = record_broker_order_event_with_fill(db, _fill_event())
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert first["fill_created"] is True
    assert replay["event_created"] is False
    assert replay["fill_created"] is False
    assert leg.filled_quantity == 10
    assert db.query(ExecutionFillRecord).count() == 1


def test_fill_validation_failure_rolls_back_callback_event(db):
    _working_intent_with_broker_order(db)
    with pytest.raises(ValueError, match="exceed"):
        record_broker_order_event_with_fill(
            db, _fill_event(cumulative_filled_quantity=51, fill_quantity=51)
        )
    assert db.query(BrokerOrderEventRecord).count() == 0
    assert db.query(ExecutionFillRecord).count() == 0
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert leg.filled_quantity == 0


def test_unmatched_broker_order_rolls_back_callback_event(db):
    _working_intent_with_broker_order(db)
    with pytest.raises(LookupError, match="not registered"):
        record_broker_order_event_with_fill(
            db, _fill_event(broker_order_id="unknown-order")
        )
    assert db.query(BrokerOrderEventRecord).count() == 0
    assert db.query(ExecutionFillRecord).count() == 0


def test_partial_fill_status_without_trade_identity_is_persisted_as_event_only(db):
    result = record_broker_order_event(
        db, _fill_event(fill_id=None, fill_quantity=None, average_fill_price=None)
    )
    assert result["created"] is True
    assert db.query(BrokerOrderEventRecord).count() == 1
    assert db.query(ExecutionFillRecord).count() == 0


def test_cumulative_quantity_mismatch_rolls_back_callback_and_fill(db):
    _working_intent_with_broker_order(db)
    with pytest.raises(ValueError, match="cumulative fill quantity"):
        record_broker_order_event_with_fill(
            db, _fill_event(cumulative_filled_quantity=12, fill_quantity=10)
        )
    assert db.query(BrokerOrderEventRecord).count() == 0
    assert db.query(ExecutionFillRecord).count() == 0


def test_new_callback_cannot_reuse_an_already_consumed_fill_identity(db):
    _working_intent_with_broker_order(db)
    record_broker_order_event_with_fill(db, _fill_event())
    with pytest.raises(BrokerEventConflict, match="different callback event"):
        record_broker_order_event_with_fill(
            db, _fill_event(event_id="evt-fill-atomic-2", cumulative_filled_quantity=20)
        )
    assert db.query(BrokerOrderEventRecord).count() == 1
    assert db.query(ExecutionFillRecord).count() == 1
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "cb"))
    assert leg.filled_quantity == 10
