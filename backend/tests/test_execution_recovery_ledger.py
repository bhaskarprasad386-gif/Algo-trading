import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.contracts import ExecutionIntent, ExecutionLeg, ExecutionMode, OrderSide
from app.execution.recovery import list_recovery_candidates
from app.execution.recovery_ledger import load_recovery_ledger_evidence
from app.execution.persistence import create_intent
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


def _candidate(db, *, account="acct-1", intent_id="intent-1"):
    intent = ExecutionIntent(
        intent_id, account, f"key-{intent_id}", "corr-1",
        ExecutionMode.PAPER, created_at_ns=100,
    )
    legs = (
        ExecutionLeg("leg-a", "NIFTY-CE", OrderSide.BUY, 50, limit_price=100),
        ExecutionLeg("leg-b", "NIFTY-PE", OrderSide.SELL, 50, limit_price=80),
    )
    create_intent(db, intent, legs)
    return list_recovery_candidates(db, account_id=account)[0]


def _add_order(db, *, intent_id, account_id, leg_id, order_id, broker="zerodha"):
    intent = db.execute(select(ExecutionIntentRecord).where(
        ExecutionIntentRecord.intent_id == intent_id,
        ExecutionIntentRecord.account_id == account_id,
    )).scalar_one()
    leg = db.execute(select(ExecutionLegRecord).where(
        ExecutionLegRecord.intent_record_id == intent.id,
        ExecutionLegRecord.leg_id == leg_id,
    )).scalar_one()
    db.add(ExecutionBrokerOrderRecord(
        intent_record_id=intent.id, leg_record_id=leg.id,
        account_id=account_id, broker=broker, broker_order_id=order_id,
        symbol=leg.instrument, side=leg.side, quantity=leg.quantity,
        client_order_id=None, accepted_at_ns=101, payload_hash="a" * 64,
    ))
    db.flush()


def test_loader_returns_only_the_selected_intents_order_and_fill_identities(db):
    candidate = _candidate(db)
    _add_order(db, intent_id="intent-1", account_id="acct-1",
               leg_id="leg-a", order_id="ord-a")
    _add_order(db, intent_id="intent-1", account_id="acct-1",
               leg_id="leg-b", order_id="ord-b")
    intent = db.execute(select(ExecutionIntentRecord).where(
        ExecutionIntentRecord.intent_id == "intent-1"
    )).scalar_one()
    leg = db.execute(select(ExecutionLegRecord).where(
        ExecutionLegRecord.intent_record_id == intent.id,
        ExecutionLegRecord.leg_id == "leg-a",
    )).scalar_one()
    db.add(ExecutionFillRecord(
        intent_record_id=intent.id, leg_record_id=leg.id,
        account_id="acct-1", broker="zerodha", broker_fill_id="fill-a",
        broker_order_id="ord-a", quantity=25, fill_price=99,
        occurred_at_ns=102, payload_hash="b" * 64,
    ))
    db.flush()

    evidence = load_recovery_ledger_evidence(
        db, candidate=candidate, broker="ZERODHA",
    )

    assert evidence.broker == "zerodha"
    assert {(x.leg_id, x.broker_order_id) for x in evidence.local_orders} == {
        ("leg-a", "ord-a"), ("leg-b", "ord-b"),
    }
    assert evidence.local_fill_quantities == {"fill-a": 25}


def test_loader_rejects_mixed_broker_ledger_instead_of_guessing(db):
    candidate = _candidate(db)
    _add_order(db, intent_id="intent-1", account_id="acct-1",
               leg_id="leg-a", order_id="ord-a", broker="fyers")

    with pytest.raises(ValueError, match="broker"):
        load_recovery_ledger_evidence(db, candidate=candidate, broker="zerodha")


def test_loader_does_not_include_another_intents_records(db):
    candidate = _candidate(db)
    _candidate(db, account="acct-1", intent_id="intent-2")
    _add_order(db, intent_id="intent-2", account_id="acct-1",
               leg_id="leg-a", order_id="other-order")

    evidence = load_recovery_ledger_evidence(
        db, candidate=candidate, broker="zerodha",
    )

    assert evidence.local_orders == ()
    assert evidence.local_fill_quantities == {}
