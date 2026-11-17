from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.broker_event_replay_review import (
    CONFIRMATION, review_operator_replay_eligibility,
)
from app.models.broker_order_event import BrokerOrderEventRecord
from app.models.execution_intent import (
    ExecutionBrokerOrderRecord, ExecutionFillRecord,
    ExecutionIntentRecord, ExecutionLegRecord,
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


def _seed(db, *, first_qty=10, second_qty=10):
    intent = ExecutionIntentRecord(
        intent_id="intent-1", account_id="acct-1", idempotency_key="key-1",
        correlation_id="corr-1", mode="REAL", status="SUBMITTING",
        created_at_ns=1, updated_at_ns=1, version=1,
    )
    db.add(intent)
    db.flush()
    legs = []
    for leg_id, symbol, side in (("leg-a", "NFO:CE", "BUY"), ("leg-b", "NFO:PE", "SELL")):
        leg = ExecutionLegRecord(
            intent_record_id=intent.id, leg_id=leg_id, instrument=symbol,
            side=side, quantity=50, order_type="LIMIT", status="PARTIALLY_FILLED",
            filled_quantity=0,
        )
        db.add(leg)
        db.flush()
        order = ExecutionBrokerOrderRecord(
            intent_record_id=intent.id, leg_record_id=leg.id,
            account_id="acct-1", broker="fyers", broker_order_id=f"ord-{leg_id}",
            symbol=symbol, side=side, quantity=50, client_order_id=None,
            accepted_at_ns=10, payload_hash="b" * 64,
        )
        db.add(order)
        legs.append((leg, order))
    db.flush()
    for leg, order, qty, suffix in (
        (legs[0][0], legs[0][1], first_qty, "a"),
        (legs[1][0], legs[1][1], second_qty, "b"),
    ):
        db.add(BrokerOrderEventRecord(
            broker="fyers", account_id="acct-1", event_id=f"evt-{suffix}",
            broker_order_id=order.broker_order_id, status="PARTIALLY_FILLED",
            cumulative_filled_quantity=qty, fill_id=f"trade-{suffix}",
            fill_quantity=qty, average_fill_price=Decimal("101.25"),
            occurred_at_ns=100 + (1 if suffix == "a" else 2),
            payload_hash="a" * 64, payload_json="{}",
        ))
    db.flush()
    return intent, legs


def _review(db, ids=("evt-a", "evt-b"), confirmation=CONFIRMATION):
    return review_operator_replay_eligibility(
        db, broker="FYERS", account_id="acct-1",
        event_ids=ids, confirmation=confirmation,
    )


def test_balanced_explicit_multi_leg_event_set_is_eligibility_only(db):
    _seed(db)
    result = _review(db)
    assert result["eligible"] is True
    assert result["status"] == "ELIGIBLE_FOR_SEPARATE_OPERATOR_REVIEW"
    assert result["replay_performed"] is False
    assert result["fill_ledger_mutated"] is False
    assert result["intent_state_mutated"] is False
    assert result["trading_authorized"] is False


def test_unbalanced_projected_multi_leg_quantities_are_blocked(db):
    _seed(db, first_qty=20, second_qty=10)
    result = _review(db)
    assert result["eligible"] is False
    assert any(
        any(reason.startswith("MULTI_LEG_PROJECTED_FILL_QUANTITY_MISMATCH") for reason in item["reasons"])
        for item in result["events"]
    )


def test_single_leg_event_set_cannot_pass_multi_leg_review(db):
    _seed(db)
    result = _review(db, ids=("evt-a",))
    assert result["eligible"] is False
    assert any("MULTI_LEG_PROJECTED_FILL_QUANTITY_MISMATCH" in reason
               for item in result["events"] for reason in item["reasons"])



def test_unselected_unresolved_fill_event_in_same_intent_blocks_subset(db):
    intent, legs = _seed(db)
    first_leg, first_order = legs[0]
    db.add(BrokerOrderEventRecord(
        broker="fyers", account_id="acct-1", event_id="evt-a-later",
        broker_order_id=first_order.broker_order_id, status="PARTIALLY_FILLED",
        cumulative_filled_quantity=15, fill_id="trade-a-later",
        fill_quantity=5, average_fill_price=Decimal("101.50"),
        occurred_at_ns=103, payload_hash="e" * 64, payload_json="{}",
    ))
    db.flush()

    result = _review(db)
    assert result["eligible"] is False
    assert any(
        "UNSELECTED_UNRESOLVED_FILL_EVENT_IN_INTENT" in reason
        for item in result["events"] for reason in item["reasons"]
    )


def test_unselected_exact_duplicate_callback_does_not_block_selection(db):
    _seed(db)
    db.add(BrokerOrderEventRecord(
        broker="fyers", account_id="acct-1", event_id="evt-a-duplicate",
        broker_order_id="ord-leg-a", status="PARTIALLY_FILLED",
        cumulative_filled_quantity=10, fill_id="trade-a",
        fill_quantity=10, average_fill_price=Decimal("101.25"),
        occurred_at_ns=101, payload_hash="f" * 64, payload_json="{}",
    ))
    db.flush()

    result = _review(db)
    assert result["eligible"] is True


def test_wrong_account_event_is_not_disclosed_or_eligible(db):
    _seed(db)
    result = _review(db, ids=("evt-other",))
    assert result["status"] == "BLOCKED"
    assert result["events"][0]["reason"] == "EVENT_NOT_FOUND_IN_BOUND_ACCOUNT"
    assert result["trading_authorized"] is False


def test_existing_fill_identity_blocks_replay_eligibility(db):
    intent, legs = _seed(db)
    leg, order = legs[0]
    db.add(ExecutionFillRecord(
        intent_record_id=intent.id, leg_record_id=leg.id, account_id="acct-1",
        broker="fyers", broker_fill_id="trade-a", broker_order_id=order.broker_order_id,
        quantity=10, fill_price=Decimal("101.25"), occurred_at_ns=101,
        payload_hash="c" * 64,
    ))
    db.flush()
    result = _review(db)
    assert result["eligible"] is False
    assert "FILL_ALREADY_PRESENT_IN_IMMUTABLE_LEDGER" in result["events"][0]["reasons"]


def test_wrong_confirmation_and_duplicate_or_empty_ids_fail_closed(db):
    _seed(db)
    with pytest.raises(ValueError, match="confirmation"):
        _review(db, confirmation="YES")
    with pytest.raises(ValueError, match="unique"):
        _review(db, ids=("evt-a", "evt-a"))
    with pytest.raises(ValueError, match="event_ids"):
        _review(db, ids=())


def test_review_does_not_mutate_fill_or_intent_state(db):
    intent, legs = _seed(db)
    before = (intent.status, intent.version, [leg.filled_quantity for leg, _ in legs])
    _review(db)
    after = (intent.status, intent.version, [leg.filled_quantity for leg, _ in legs])
    assert after == before
    assert db.query(ExecutionFillRecord).count() == 0


def test_replay_eligibility_audit_record_is_append_only_evidence_metadata(db):
    from app.models.broker_replay_eligibility_audit import BrokerReplayEligibilityAuditRecord

    audit = BrokerReplayEligibilityAuditRecord(
        review_id="review-1", broker="fyers", account_id="acct-1",
        event_ids_json='["evt-a","evt-b"]', evidence_digest="d" * 64,
        result_status="ELIGIBLE_FOR_SEPARATE_OPERATOR_REVIEW",
        eligible=True, reviewed_at_ns=1_000, valid_until_ns=61_000,
    )
    db.add(audit)
    db.flush()
    stored = db.query(BrokerReplayEligibilityAuditRecord).filter_by(review_id="review-1").one()
    assert stored.evidence_digest == "d" * 64
    assert stored.valid_until_ns - stored.reviewed_at_ns == 60_000
    assert stored.eligible is True
