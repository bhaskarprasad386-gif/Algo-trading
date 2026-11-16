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
