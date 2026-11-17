"""Five-broker integration conformance for durable tradebook fill ingestion.

These tests use synthetic normalized snapshots and an in-memory database. They
exercise the shared ingestion boundary, not live broker APIs or order routing.
"""
from types import SimpleNamespace

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
    ExecutionIntent,
    ExecutionLeg,
    ExecutionMode,
    IntentStatus,
    OrderSide,
)
from app.execution.persistence import (
    create_intent,
    record_broker_order,
    transition_intent,
)
from app.execution.broker_tradebook_fill_sync import sync_account_tradebook_fills
from app.models.execution_intent import (
    ExecutionFillRecord,
    ExecutionIntentRecord,
    ExecutionLegRecord,
)


BROKERS = ("angel_one", "fyers", "mstock", "shoonya", "zerodha")


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


def _working_intent(db, broker):
    intent = ExecutionIntent(
        f"intent-{broker}", "acct-1", f"key-{broker}", f"corr-{broker}",
        ExecutionMode.PAPER, created_at_ns=100,
    )
    legs = (
        ExecutionLeg("leg-1", "NFO:NIFTY26NOV25000CE", OrderSide.BUY, 50, limit_price=100),
    )
    create_intent(db, intent, legs)
    saved = None
    for target in (
        IntentStatus.VALIDATING,
        IntentStatus.RISK_CHECK,
        IntentStatus.APPROVED,
        IntentStatus.RESERVED,
        IntentStatus.SUBMITTING,
        IntentStatus.WORKING,
    ):
        saved = transition_intent(
            db,
            account_id="acct-1",
            intent_id=intent.intent_id,
            expected_version=0 if saved is None else saved.version,
            target=target,
        )
    record_broker_order(
        db,
        account_id="acct-1",
        intent_id=intent.intent_id,
        leg_id="leg-1",
        broker=broker,
        broker_order_id=f"order-{broker}",
        symbol="NFO:NIFTY26NOV25000CE",
        side="BUY",
        quantity=50,
        accepted_at_ns=200,
    )
    return intent.intent_id


def _adapter(broker, *, account_id="acct-1", snapshot_account="acct-1", complete=True):
    snapshot = BrokerSnapshot(
        account_id=snapshot_account,
        broker=broker,
        orders=(
            BrokerOrderSnapshot(
                broker_order_id=f"order-{broker}",
                symbol="NFO:NIFTY26NOV25000CE",
                side="BUY",
                quantity=50,
                filled_quantity=25,
                raw_status="PARTIALLY_FILLED",
            ),
        ),
        trades=(
            BrokerTradeSnapshot(
                broker_trade_id=f"trade-{broker}",
                broker_order_id=f"order-{broker}",
                quantity=25,
                fill_price=101.25,
                occurred_at_ns=1_800_000_000_000_000_000,
            ),
        ),
        complete=complete,
    )
    history = SimpleNamespace(
        snapshot=snapshot,
        scope="CURRENT_TRADING_DAY",
        trading_date="2026-11-16",
    )
    return SimpleNamespace(
        name=broker,
        account_id=account_id,
        connected=True,
        fetch_today_broker_snapshot=lambda **kwargs: history,
    )


@pytest.mark.parametrize("broker", BROKERS)
def test_each_broker_normalized_tradebook_fill_is_durably_persisted_and_replay_safe(db, broker):
    intent_id = _working_intent(db, broker)
    adapter = _adapter(broker)

    first = sync_account_tradebook_fills(db, adapter=adapter, account_id="acct-1")
    replay = sync_account_tradebook_fills(db, adapter=adapter, account_id="acct-1")

    fill = db.scalar(select(ExecutionFillRecord))
    leg = db.scalar(select(ExecutionLegRecord))
    intent = db.scalar(select(ExecutionIntentRecord))

    assert first["status"] == "SYNCED"
    assert first["created_count"] == 1
    assert replay["created_count"] == 0
    assert replay["duplicate_count"] == 1
    assert fill.broker == broker
    assert fill.broker_fill_id == f"trade-{broker}"
    assert fill.intent.intent_id == intent_id
    assert fill.intent_record_id == intent.id
    assert leg.filled_quantity == 25
    assert leg.status == "PARTIALLY_FILLED"
    assert intent.status == IntentStatus.PARTIALLY_FILLED.value
    assert db.query(ExecutionFillRecord).count() == 1


@pytest.mark.parametrize("broker", BROKERS)
def test_each_broker_fill_sync_rejects_cross_account_or_incomplete_snapshot_before_writes(db, broker):
    _working_intent(db, broker)

    wrong_account = sync_account_tradebook_fills(
        db,
        adapter=_adapter(broker, snapshot_account="acct-other"),
        account_id="acct-1",
    )
    incomplete = sync_account_tradebook_fills(
        db,
        adapter=_adapter(broker, complete=False),
        account_id="acct-1",
    )

    assert wrong_account["status"] == "BLOCKED"
    assert wrong_account["reason"] == "BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH"
    assert incomplete["status"] == "BLOCKED"
    assert incomplete["reason"] == "BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH"
    assert db.query(ExecutionFillRecord).count() == 0
    leg = db.scalar(select(ExecutionLegRecord))
    assert leg.filled_quantity == 0
