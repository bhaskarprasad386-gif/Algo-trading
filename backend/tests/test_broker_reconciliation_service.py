import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.broker_reconciliation import (
    BrokerOrderSnapshot,
    BrokerSnapshot,
    BrokerTradeSnapshot,
)
from app.execution.contracts import (
    ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide,
)
from app.execution.persistence import create_intent, record_broker_order, record_fill, transition_intent
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


def _persist_order(db, *, account_id="acct-1", all_legs=True):
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

    order_legs = legs if all_legs else legs[:1]
    for index, leg in enumerate(order_legs, start=1):
        record_broker_order(
            db, account_id=account_id, intent_id="intent-reconcile",
            leg_id=leg.leg_id, broker="fyers",
            broker_order_id=f"order-{index}", symbol=leg.instrument,
            side=leg.side.value, quantity=leg.quantity,
            accepted_at_ns=200 + index,
        )


def _snapshot(*, account_id="acct-1", filled=0, complete=True, symbol="NFO:CE"):
    rows = (
        ("order-1", symbol, "BUY"),
        ("order-2", "NFO:CE-HIGH", "SELL"),
        ("order-3", "NFO:PE", "BUY"),
        ("order-4", "NFO:PE-HIGH", "SELL"),
    )
    return BrokerSnapshot(
        account_id=account_id,
        broker="FYERS",
        orders=tuple(BrokerOrderSnapshot(
            broker_order_id=order_id, symbol=order_symbol, side=side,
            quantity=50, filled_quantity=filled if index == 0 else 0,
            raw_status="OPEN",
        ) for index, (order_id, order_symbol, side) in enumerate(rows)),
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


def test_unrecorded_broker_order_is_durably_blocked(db):
    _persist_order(db)
    snapshot = BrokerSnapshot(
        account_id="acct-1",
        broker="fyers",
        orders=_snapshot().orders + (
            BrokerOrderSnapshot(
                broker_order_id="unexpected-order", symbol="NFO:OTHER", side="SELL",
                quantity=25, filled_quantity=0,
            ),
        ),
        trades=(),
        complete=True,
    )
    result = reconcile_persisted_account_snapshot(
        db, snapshot=snapshot, reconciliation_id="run-unrecorded-order",
        intent_ids=("intent-reconcile",),
    )
    assert result["report"].is_consistent is False
    assert any(
        issue.status.value == "UNRECORDED_BROKER_ORDER"
        for issue in result["report"].issues
    )


def test_missing_broker_order_identity_on_any_persisted_leg_blocks_reconciliation(db):
    _persist_order(db, all_legs=False)
    result = reconcile_persisted_account_snapshot(
        db, snapshot=_snapshot(), reconciliation_id="run-missing-leg-order",
        intent_ids=("intent-reconcile",),
    )
    statuses = {issue.status.value for issue in result["report"].issues}
    assert "MISSING_PERSISTED_BROKER_ORDER_ID" in statuses
    assert result["report"].is_consistent is False
    stored = db.scalar(select(BrokerReconciliationRecord))
    assert stored.is_consistent is False
    assert stored.issue_count >= 1


def test_raw_status_is_durably_preserved_and_unknown_status_blocks_consistency(db):
    import json

    _persist_order(db)
    snapshot = _snapshot()
    snapshot = BrokerSnapshot(
        account_id=snapshot.account_id,
        broker=snapshot.broker,
        orders=tuple(
            BrokerOrderSnapshot(
                broker_order_id=order.broker_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                filled_quantity=order.filled_quantity,
                raw_status="VENDOR_STATUS_NOT_MAPPED" if index == 0 else "OPEN",
            )
            for index, order in enumerate(snapshot.orders)
        ),
        trades=snapshot.trades,
        complete=snapshot.complete,
    )
    result = reconcile_persisted_account_snapshot(
        db, snapshot=snapshot, reconciliation_id="run-unknown-status",
        intent_ids=("intent-reconcile",),
    )
    stored = db.scalar(select(BrokerReconciliationRecord))
    payload = json.loads(stored.report_json)
    assert result["report"].is_consistent is False
    assert any(
        issue.status.value == "ORDER_STATUS_REQUIRES_RECONCILIATION"
        for issue in result["report"].issues
    )
    assert payload["source_snapshot"]["orders"][0]["raw_status"] == "VENDOR_STATUS_NOT_MAPPED"
    assert payload["order_status_evidence"][0]["normalized_state"] == "UNKNOWN"
    assert payload["order_status_evidence"][0]["requires_reconciliation"] is True


def test_cancel_after_partial_fill_is_persisted_as_conflict_free_manual_reconciliation(db):
    _persist_order(db)
    snapshot = _snapshot(filled=10)
    first = snapshot.orders[0]
    cancelled = BrokerOrderSnapshot(
        broker_order_id=first.broker_order_id,
        symbol=first.symbol,
        side=first.side,
        quantity=first.quantity,
        filled_quantity=first.filled_quantity,
        raw_status="CANCELLED",
    )
    snapshot = BrokerSnapshot(
        account_id=snapshot.account_id,
        broker=snapshot.broker,
        orders=(cancelled,) + snapshot.orders[1:],
        trades=(),
        complete=True,
    )
    result = reconcile_persisted_account_snapshot(
        db, snapshot=snapshot, reconciliation_id="run-partial-cancel",
        intent_ids=("intent-reconcile",),
    )
    assert result["report"].is_consistent is False
    assert any(
        issue.status.value == "ORDER_STATUS_REQUIRES_RECONCILIATION"
        for issue in result["report"].issues
    )


def _snapshot_with_fill(*, fill_price=101.25, occurred_at_ns=123456789):
    base = _snapshot(filled=25)
    orders = tuple(
        BrokerOrderSnapshot(
            broker_order_id=row.broker_order_id,
            symbol=row.symbol,
            side=row.side,
            quantity=row.quantity,
            filled_quantity=25,
            raw_status="OPEN",
        )
        for row in base.orders
    )
    return BrokerSnapshot(
        account_id=base.account_id,
        broker=base.broker,
        orders=orders,
        trades=(
            BrokerTradeSnapshot("trade-1", "order-1", 25, fill_price, occurred_at_ns),
            BrokerTradeSnapshot("trade-2", "order-2", 25, 81.25, 123456790),
            BrokerTradeSnapshot("trade-3", "order-3", 25, 91.25, 123456791),
            BrokerTradeSnapshot("trade-4", "order-4", 25, 71.25, 123456792),
        ),
        complete=True,
    )


def _persist_one_fill(db, *, price="101.25", occurred_at_ns=123456789):
    fills = (
        ("cb", "trade-1", "order-1", price, occurred_at_ns),
        ("cs", "trade-2", "order-2", "81.25", 123456790),
        ("pb", "trade-3", "order-3", "91.25", 123456791),
        ("ps", "trade-4", "order-4", "71.25", 123456792),
    )
    for leg_id, trade_id, order_id, fill_price, timestamp in fills:
        record_fill(
            db, account_id="acct-1", intent_id="intent-reconcile", leg_id=leg_id,
            broker="fyers", broker_fill_id=trade_id, broker_order_id=order_id,
            quantity=25, fill_price=fill_price, occurred_at_ns=timestamp,
        )


def test_reconciliation_compares_persisted_fill_price_and_timestamp(db):
    _persist_order(db)
    _persist_one_fill(db)
    result = reconcile_persisted_account_snapshot(
        db, snapshot=_snapshot_with_fill(), reconciliation_id="run-fill-evidence-match",
        intent_ids=("intent-reconcile",),
    )
    assert result["report"].is_consistent is True


def test_reconciliation_durably_blocks_fill_price_and_timestamp_mismatch(db):
    _persist_order(db)
    _persist_one_fill(db)
    result = reconcile_persisted_account_snapshot(
        db, snapshot=_snapshot_with_fill(fill_price=101.5, occurred_at_ns=123456790),
        reconciliation_id="run-fill-evidence-mismatch",
        intent_ids=("intent-reconcile",),
    )
    statuses = {issue.status.value for issue in result["report"].issues}
    assert "FILL_PRICE_MISMATCH" in statuses
    assert "FILL_TIMESTAMP_MISMATCH" in statuses
    stored = db.scalar(select(BrokerReconciliationRecord).where(
        BrokerReconciliationRecord.reconciliation_id == "run-fill-evidence-mismatch"
    ))
    assert stored.is_consistent is False


def test_multileg_spread_with_unequal_broker_fills_is_blocked(db):
    _persist_order(db)
    # Only the first of the four Box Spread legs has filled at the broker.
    result = reconcile_persisted_account_snapshot(
        db, snapshot=_snapshot(filled=25),
        reconciliation_id="run-unequal-multileg-fills",
        intent_ids=("intent-reconcile",),
    )
    issue = next(
        issue for issue in result["report"].issues
        if issue.status.value == "MULTI_LEG_FILL_QUANTITY_MISMATCH"
    )
    assert issue.identity == "intent-reconcile"
    assert "cb=25" in issue.detail
    assert "cs=0" in issue.detail
    assert result["report"].is_consistent is False
    stored = db.scalar(select(BrokerReconciliationRecord).where(
        BrokerReconciliationRecord.reconciliation_id == "run-unequal-multileg-fills"
    ))
    assert stored.is_consistent is False


def test_multileg_spread_with_equal_partial_fills_has_no_leg_imbalance_issue(db):
    _persist_order(db)
    base = _snapshot()
    balanced = BrokerSnapshot(
        account_id=base.account_id,
        broker=base.broker,
        orders=tuple(
            BrokerOrderSnapshot(
                broker_order_id=row.broker_order_id,
                symbol=row.symbol,
                side=row.side,
                quantity=row.quantity,
                filled_quantity=10,
                raw_status="OPEN",
            )
            for row in base.orders
        ),
        trades=(),
        complete=True,
    )
    result = reconcile_persisted_account_snapshot(
        db, snapshot=balanced,
        reconciliation_id="run-balanced-partial-multileg-fills",
        intent_ids=("intent-reconcile",),
    )
    assert not any(
        issue.status.value == "MULTI_LEG_FILL_QUANTITY_MISMATCH"
        for issue in result["report"].issues
    )
