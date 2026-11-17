"""End-to-end handoff coverage for all five read-only broker history contracts.

These tests use typed provider-window evidence and an in-memory persistence DB.
They never construct a live broker client or call a broker endpoint.
"""
from datetime import datetime, timedelta, timezone

import json
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.brokers.history import BrokerHistoryWindow
from app.execution.broker_reconciliation import BrokerOrderSnapshot, BrokerSnapshot
from app.execution.contracts import (
    ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide,
)
from app.execution.persistence import create_intent, record_broker_order, transition_intent
from app.execution.reconciliation_service import (
    ReconciliationIdentityConflict,
    reconcile_persisted_history_window,
)
from app.models.broker_reconciliation import BrokerReconciliationRecord


BROKERS = (
    ("angel_one", "ANGEL_ONE_CURRENT_DAY"),
    ("zerodha", "ZERODHA_CURRENT_DAY"),
    ("fyers", "FYERS_CLOSED_DAY_REPORTS"),
    ("shoonya", "SHOONYA_CURRENT_DAY"),
    ("mstock", "MSTOCK_CURRENT_DAY"),
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


def _persist_four_leg_intent(db, broker):
    account_id = "account-history-handoff"
    intent_id = f"intent-{broker}"
    intent = ExecutionIntent(
        intent_id, account_id, f"key-{broker}", f"correlation-{broker}",
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
        IntentStatus.VALIDATING,
        IntentStatus.RISK_CHECK,
        IntentStatus.APPROVED,
        IntentStatus.RESERVED,
        IntentStatus.SUBMITTING,
    ):
        saved = transition_intent(
            db, account_id=account_id, intent_id=intent_id,
            expected_version=version, target=target,
        )
        version = saved.version

    for index, leg in enumerate(legs, start=1):
        record_broker_order(
            db, account_id=account_id, intent_id=intent_id,
            leg_id=leg.leg_id, broker=broker,
            broker_order_id=f"{broker}-order-{index}",
            symbol=leg.instrument, side=leg.side.value,
            quantity=leg.quantity, accepted_at_ns=200 + index,
        )
    return account_id, intent_id


def _complete_window(account_id, broker, provider_window):
    start = datetime(2026, 11, 16, tzinfo=timezone.utc)
    orders = tuple(
        BrokerOrderSnapshot(
            broker_order_id=f"{broker}-order-{index}",
            symbol=symbol,
            side=side,
            quantity=50,
            filled_quantity=0,
            raw_status="OPEN",
        )
        for index, (symbol, side) in enumerate((
            ("NFO:CE", "BUY"),
            ("NFO:CE-HIGH", "SELL"),
            ("NFO:PE", "BUY"),
            ("NFO:PE-HIGH", "SELL"),
        ), start=1)
    )
    return BrokerHistoryWindow(
        snapshot=BrokerSnapshot(
            account_id=account_id, broker=broker,
            orders=orders, trades=(), complete=True,
        ),
        window_start=start,
        window_end=start + timedelta(days=1),
        pages_fetched=2 if broker == "fyers" else 1,
        has_more=False,
        provider_window=provider_window,
    )


@pytest.mark.parametrize(("broker", "provider_window"), BROKERS)
def test_each_broker_history_window_persists_through_reconciliation_handoff(
    db, broker, provider_window,
):
    account_id, intent_id = _persist_four_leg_intent(db, broker)
    window = _complete_window(account_id, broker, provider_window)

    result = reconcile_persisted_history_window(
        db, history_window=window,
        reconciliation_id=f"handoff-{broker}",
        intent_ids=(intent_id,),
    )

    stored = db.scalar(select(BrokerReconciliationRecord).where(
        BrokerReconciliationRecord.reconciliation_id == f"handoff-{broker}"
    ))
    payload = json.loads(stored.report_json)

    assert result["created"] is True
    assert result["report"].is_consistent is True
    assert stored.account_id == account_id
    assert stored.broker == broker
    assert stored.snapshot_complete is True
    assert payload["history_window"]["provider_window"] == provider_window
    assert payload["history_window"]["pages_fetched"] == window.pages_fetched
    assert payload["history_window"]["complete"] is True
    assert payload["history_window"]["has_more"] is False
    assert payload["history_window"]["next_cursor"] is None


def test_same_reconciliation_id_rejects_changed_provider_window_evidence(db):
    broker = "zerodha"
    account_id, intent_id = _persist_four_leg_intent(db, broker)
    first_window = _complete_window(account_id, broker, "ZERODHA_CURRENT_DAY")
    reconcile_persisted_history_window(
        db, history_window=first_window,
        reconciliation_id="handoff-window-identity",
        intent_ids=(intent_id,),
    )

    changed_window = _complete_window(account_id, broker, "ZERODHA_RELABELED_WINDOW")
    with pytest.raises(ReconciliationIdentityConflict):
        reconcile_persisted_history_window(
            db, history_window=changed_window,
            reconciliation_id="handoff-window-identity",
            intent_ids=(intent_id,),
        )

    assert db.query(BrokerReconciliationRecord).filter_by(
        reconciliation_id="handoff-window-identity"
    ).count() == 1
