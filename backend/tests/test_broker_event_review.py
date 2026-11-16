from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.broker_event_review import review_deferred_broker_events
from app.models.broker_order_event import BrokerOrderEventRecord
from app.models.execution_intent import (
    ExecutionBrokerOrderRecord,
    ExecutionFillRecord,
    ExecutionIntentRecord,
    ExecutionLegRecord,
)


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


def _event(*, account_id="acct-1", event_id="evt-1", fill_id="trade-1",
           fill_quantity=10, cumulative=20, status="PARTIALLY_FILLED",
           occurred_at_ns=200):
    row = BrokerOrderEventRecord(
        broker="fyers", account_id=account_id, event_id=event_id,
        broker_order_id="order-1", status=status,
        cumulative_filled_quantity=cumulative, fill_id=fill_id,
        fill_quantity=fill_quantity,
        average_fill_price=Decimal("101.25") if fill_id else None,
        occurred_at_ns=occurred_at_ns, payload_hash="a" * 64, payload_json="{}",
    )
    return row


def _registered_order(db, *, quantity=50):
    intent = ExecutionIntentRecord(
        intent_id="intent-1", account_id="acct-1", idempotency_key="key-1",
        correlation_id="corr-1", mode="REAL", status="SUBMITTING",
        created_at_ns=1, updated_at_ns=1, version=1,
    )
    db.add(intent)
    db.flush()
    leg = ExecutionLegRecord(
        intent_record_id=intent.id, leg_id="cb", instrument="NFO:CE",
        side="BUY", quantity=quantity, order_type="LIMIT",
        status="PARTIALLY_FILLED", filled_quantity=10,
    )
    db.add(leg)
    db.flush()
    order = ExecutionBrokerOrderRecord(
        intent_record_id=intent.id, leg_record_id=leg.id,
        account_id="acct-1", broker="fyers", broker_order_id="order-1",
        symbol="NFO:CE", side="BUY", quantity=quantity,
        client_order_id=None, accepted_at_ns=10, payload_hash="b" * 64,
    )
    db.add(order)
    db.flush()
    return intent, leg, order


def test_deferred_cumulative_gap_is_visible_without_applying_it(db):
    _registered_order(db)
    db.add(_event())
    db.flush()

    result = review_deferred_broker_events(
        db, broker="FYERS", account_id="acct-1",
    )

    assert result["status"] == "REQUIRES_REVIEW"
    assert result["event_count"] == 1
    assert result["deferred_count"] == 1
    assert result["events"][0]["reason"] == "CUMULATIVE_FILL_GAP"
    assert result["events"][0]["broker_order_id"] == "order-1"
    assert result["trading_authorized"] is False


def test_applied_fill_is_not_reported_as_deferred_and_account_is_scoped(db):
    intent, leg, _ = _registered_order(db)
    db.add(_event(cumulative=10))
    db.add(ExecutionFillRecord(
        intent_record_id=intent.id, leg_record_id=leg.id,
        account_id="acct-1", broker="fyers", broker_fill_id="trade-1",
        broker_order_id="order-1", quantity=10, fill_price=Decimal("101.25"),
        occurred_at_ns=200, payload_hash="c" * 64,
    ))
    db.add(_event(account_id="acct-2", event_id="other-account"))
    db.flush()

    result = review_deferred_broker_events(
        db, broker="fyers", account_id="acct-1",
    )

    assert result["status"] == "CLEAR"
    assert result["event_count"] == 1
    assert result["unresolved_count"] == 0
    assert result["events"] == []


def test_fill_status_without_trade_identity_is_flagged(db):
    db.add(_event(fill_id=None, fill_quantity=None, cumulative=5))
    db.flush()

    result = review_deferred_broker_events(
        db, broker="fyers", account_id="acct-1",
    )

    assert result["unresolved_count"] == 1
    assert result["events"][0]["reason"] == "FILL_IDENTITY_MISSING"


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5])
def test_review_limit_is_bounded(limit):
    with pytest.raises(ValueError, match="limit"):
        review_deferred_broker_events(
            object(), broker="fyers", account_id="acct-1", limit=limit,
        )



def test_reused_fill_identity_with_different_price_is_not_hidden(db):
    intent, leg, _ = _registered_order(db)
    db.add(_event(cumulative=10))
    db.add(ExecutionFillRecord(
        intent_record_id=intent.id, leg_record_id=leg.id,
        account_id="acct-1", broker="fyers", broker_fill_id="trade-1",
        broker_order_id="order-1", quantity=10, fill_price=Decimal("102.00"),
        occurred_at_ns=200, payload_hash="d" * 64,
    ))
    db.flush()

    result = review_deferred_broker_events(
        db, broker="fyers", account_id="acct-1",
    )

    assert result["unresolved_count"] == 1
    assert result["events"][0]["reason"] == "FILL_IDENTITY_EVIDENCE_CONFLICT"
