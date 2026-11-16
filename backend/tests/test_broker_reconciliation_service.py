import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.broker_reconciliation import (
    BrokerOrderSnapshot,
    BrokerSnapshot,
)
from app.execution.contracts import (
    ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide,
)
from app.execution.persistence import create_intent, record_broker_order, transition_intent
from app.execution.reconciliation_service import (
    ReconciliationIdentityConflict,
    reconcile_persisted_account_snapshot,
)
from app.models.broker_reconciliation import BrokerReconciliationRecord


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


def _persist_order(db, *, account_id="acct-1"):
    intent = ExecutionIntent(
        "intent-reconcile", account_id, "key-reconcile", "corr-reconcile",
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
            db, account_id=account_id, intent_id="intent-reconcile",
            expected_version=version, target=target,
        )
        version = saved.version
    record_broker_order(
        db, account_id=account_id, intent_id="intent-reconcile", leg_id="cb",
        broker="fyers", broker_order_id="order-1", symbol="NFO:CE",
        side="BUY", quantity=50, accepted_at_ns=200,
    )


def _snapshot(*, account_id="acct-1", filled=0, complete=True, symbol="NFO:CE"):
    return BrokerSnapshot(
        account_id=account_id,
        broker="FYERS",
        orders=(BrokerOrderSnapshot(
            broker_order_id="order-1", symbol=symbol, side="BUY",
            quantity=50, filled_quantity=filled,
        ),),
        trades=(),
        complete=complete,
    )


def test_persists_consistent_account_scoped_reconciliation(db):
    _persist_order(db)
    result = reconcile_persisted_account_snapshot(
        db, snapshot=_snapshot(), reconciliation_id="run-1",
        intent_ids=("intent-reconcile",),
    )
    stored = db.scalar(select(BrokerReconciliationRecord))
    assert result["created"] is True
    assert result["report"].is_consistent is True
    assert stored.account_id == "acct-1"
    assert stored.broker == "fyers"
    assert stored.is_consistent is True
    assert stored.issue_count == 0


def test_incomplete_snapshot_is_durably_blocked(db):
    _persist_order(db)
    result = reconcile_persisted_account_snapshot(
        db, snapshot=_snapshot(complete=False), reconciliation_id="run-incomplete",
        intent_ids=("intent-reconcile",),
    )
    stored = db.scalar(select(BrokerReconciliationRecord))
    assert result["report"].is_consistent is False
    assert stored.snapshot_complete is False
    assert stored.is_consistent is False
    assert stored.issue_count >= 1


def test_broker_order_mismatch_is_durably_blocked(db):
    _persist_order(db)
    result = reconcile_persisted_account_snapshot(
        db, snapshot=_snapshot(symbol="NFO:WRONG"), reconciliation_id="run-mismatch",
        intent_ids=("intent-reconcile",),
    )
    assert result["report"].is_consistent is False
    assert any(issue.status.value == "BROKER_ORDER_MISMATCH" for issue in result["report"].issues)


def test_exact_reconciliation_replay_is_idempotent(db):
    _persist_order(db)
    first = reconcile_persisted_account_snapshot(
        db, snapshot=_snapshot(), reconciliation_id="run-replay",
        intent_ids=("intent-reconcile",),
    )
    replay = reconcile_persisted_account_snapshot(
        db, snapshot=_snapshot(), reconciliation_id="run-replay",
        intent_ids=("intent-reconcile",),
    )
    assert first["created"] is True
    assert replay["created"] is False
    assert db.query(BrokerReconciliationRecord).count() == 1


def test_reconciliation_id_cannot_be_reused_for_changed_evidence(db):
    _persist_order(db)
    reconcile_persisted_account_snapshot(
        db, snapshot=_snapshot(), reconciliation_id="run-conflict",
        intent_ids=("intent-reconcile",),
    )
    with pytest.raises(ReconciliationIdentityConflict):
        reconcile_persisted_account_snapshot(
            db, snapshot=_snapshot(symbol="NFO:CHANGED"),
            reconciliation_id="run-conflict", intent_ids=("intent-reconcile",),
        )


def test_intent_lookup_is_account_scoped(db):
    _persist_order(db)
    with pytest.raises(LookupError, match="do not belong"):
        reconcile_persisted_account_snapshot(
            db, snapshot=_snapshot(account_id="acct-2"),
            reconciliation_id="run-cross-account", intent_ids=("intent-reconcile",),
        )
