from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.broker_event_replay_preview import preview_deferred_fill_replay
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


def _setup(db, *, event_cumulative=20, fill_quantity=10, status="PARTIALLY_FILLED"):
    intent = ExecutionIntentRecord(
        intent_id="intent-preview", account_id="acct-1",
        idempotency_key="key-preview", correlation_id="corr-preview",
        mode="REAL", status="SUBMITTING", created_at_ns=1,
        updated_at_ns=1, version=1,
    )
    db.add(intent)
    db.flush()
    leg = ExecutionLegRecord(
        intent_record_id=intent.id, leg_id="cb", instrument="NFO:CE",
        side="BUY", quantity=50, order_type="LIMIT",
        status="PARTIALLY_FILLED", filled_quantity=0,
    )
    db.add(leg)
    db.flush()
    order = ExecutionBrokerOrderRecord(
        intent_record_id=intent.id, leg_record_id=leg.id,
        account_id="acct-1", broker="fyers", broker_order_id="order-1",
        symbol="NFO:CE", side="BUY", quantity=50,
        client_order_id=None, accepted_at_ns=10, payload_hash="b" * 64,
    )
    db.add(order)
    db.flush()
    event = BrokerOrderEventRecord(
        broker="fyers", account_id="acct-1", event_id="evt-preview",
        broker_order_id="order-1", status=status,
        cumulative_filled_quantity=event_cumulative, fill_id="trade-preview",
        fill_quantity=fill_quantity, average_fill_price=101.25,
        occurred_at_ns=200, payload_hash="a" * 64, payload_json="{}",
    )
    db.add(event)
    db.flush()
    return intent, leg, order, event


def test_cumulative_gap_is_not_replay_eligible_and_nothing_mutates(db):
    _setup(db, event_cumulative=20, fill_quantity=10)

    result = preview_deferred_fill_replay(
        db, broker="FYERS", account_id="acct-1", event_id="evt-preview",
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "CUMULATIVE_FILL_GAP"
    assert result["replay_performed"] is False
    assert result["trading_authorized"] is False
    assert db.query(ExecutionFillRecord).count() == 0


def test_contiguous_partial_fill_is_only_eligible_for_operator_review(db):
    _setup(db, event_cumulative=10, fill_quantity=10)

    result = preview_deferred_fill_replay(
        db, broker="fyers", account_id="acct-1", event_id="evt-preview",
    )

    assert result["status"] == "ELIGIBLE_FOR_EXPLICIT_OPERATOR_REVIEW"
    assert result["reason"] == "CONTIGUOUS_FILL_EVIDENCE"
    assert result["replay_performed"] is False
    assert result["trading_authorized"] is False
    assert db.query(ExecutionFillRecord).count() == 0


def test_filled_status_must_match_registered_order_quantity(db):
    _setup(db, event_cumulative=10, fill_quantity=10, status="FILLED")

    result = preview_deferred_fill_replay(
        db, broker="fyers", account_id="acct-1", event_id="evt-preview",
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "FILLED_STATUS_QUANTITY_CONFLICT"


def test_unknown_event_is_blocked_without_mutation(db):
    result = preview_deferred_fill_replay(
        db, broker="fyers", account_id="acct-1", event_id="not-present",
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "EVENT_NOT_FOUND"
    assert result["replay_performed"] is False


def test_exact_already_applied_fill_is_not_replayed(db):
    intent, leg, _, event = _setup(db, event_cumulative=10, fill_quantity=10)
    db.add(ExecutionFillRecord(
        intent_record_id=intent.id, leg_record_id=leg.id,
        account_id="acct-1", broker="fyers", broker_fill_id=event.fill_id,
        broker_order_id=event.broker_order_id, quantity=10,
        fill_price=Decimal("101.25"), occurred_at_ns=200,
        payload_hash="c" * 64,
    ))
    db.flush()

    result = preview_deferred_fill_replay(
        db, broker="fyers", account_id="acct-1", event_id="evt-preview",
    )

    assert result["status"] == "ALREADY_APPLIED"
    assert result["replay_performed"] is False
    assert db.query(ExecutionFillRecord).count() == 1


@pytest.mark.parametrize("field,value", [
    ("broker", ""), ("account_id", ""), ("event_id", ""),
])
def test_required_scope_fields_are_validated(db, field, value):
    args = {"broker": "fyers", "account_id": "acct-1", "event_id": "evt"}
    args[field] = value
    with pytest.raises(ValueError):
        preview_deferred_fill_replay(db, **args)
