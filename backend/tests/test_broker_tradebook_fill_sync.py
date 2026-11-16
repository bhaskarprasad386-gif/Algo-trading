from types import SimpleNamespace
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.contracts import ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide
from app.execution.persistence import create_intent, record_broker_order, transition_intent
from app.execution.broker_reconciliation import BrokerOrderSnapshot, BrokerSnapshot, BrokerTradeSnapshot
from app.execution import broker_tradebook_fill_sync as sync
from app.models.execution_intent import ExecutionFillRecord, ExecutionIntentRecord, ExecutionLegRecord


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


def _working_intent(db, order_count=1):
    intent = ExecutionIntent(
        "intent-1", "acct-1", "key-1", "corr-1",
        ExecutionMode.PAPER, created_at_ns=100,
    )
    legs = tuple(
        ExecutionLeg(leg_id, symbol, side, 50, limit_price=100)
        for leg_id, symbol, side in (
            ("ce-buy", "NFO:CE", OrderSide.BUY),
            ("ce-sell", "NFO:CE-HIGH", OrderSide.SELL),
            ("pe-buy", "NFO:PE", OrderSide.BUY),
            ("pe-sell", "NFO:PE-HIGH", OrderSide.SELL),
        )
    )
    create_intent(db, intent, legs)
    current = IntentStatus.CREATED
    saved = None
    for target in (
        IntentStatus.VALIDATING, IntentStatus.RISK_CHECK, IntentStatus.APPROVED,
        IntentStatus.RESERVED, IntentStatus.SUBMITTING, IntentStatus.WORKING,
    ):
        saved = transition_intent(
            db, account_id="acct-1", intent_id="intent-1",
            expected_version=0 if saved is None else saved.version, target=target,
        )
        current = target

    order_specs = (
        ("ce-buy", "NFO:CE", "BUY", "order-ce"),
        ("ce-sell", "NFO:CE-HIGH", "SELL", "order-cs"),
    )
    for leg_id, symbol, side, order_id in order_specs[:order_count]:
        record_broker_order(
            db, account_id="acct-1", intent_id="intent-1", leg_id=leg_id,
            broker="angel_one", broker_order_id=order_id, symbol=symbol,
            side=side, quantity=50, accepted_at_ns=200,
        )


def _snapshot(*, trades=None, filled=25, broker="angel_one", account="acct-1", complete=True):
    orders = [BrokerOrderSnapshot(
        broker_order_id="order-ce", symbol="NFO:CE", side="BUY",
        quantity=50, filled_quantity=filled, raw_status="PARTIALLY_FILLED",
    )]
    if trades is not None:
        trade_rows = tuple(trades)
    else:
        trade_rows = (BrokerTradeSnapshot("trade-1", "order-ce", 25, 101.25, 1_800_000_000_000_000_000),)
    return BrokerSnapshot(account, broker, tuple(orders), trade_rows, complete)


def _adapter(snapshot, *, connected=True, account_id="acct-1", name="angel_one", scope="CURRENT_TRADING_DAY"):
    history = SimpleNamespace(snapshot=snapshot, scope=scope, trading_date="2026-11-16")
    return SimpleNamespace(
        name=name, account_id=account_id, connected=connected,
        fetch_today_broker_snapshot=lambda **kwargs: history,
    )


def test_tradebook_sync_persists_real_fill_and_updates_leg_atomically(db):
    _working_intent(db)
    result = sync.sync_account_tradebook_fills(
        db, adapter=_adapter(_snapshot()), account_id="acct-1",
    )
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "ce-buy"))
    fill = db.scalar(select(ExecutionFillRecord))
    intent = db.scalar(select(ExecutionIntentRecord))
    assert result["status"] == "SYNCED"
    assert result["created_count"] == 1
    assert fill.broker_fill_id == "trade-1"
    assert Decimal(fill.fill_price) == Decimal("101.25000000")
    assert leg.filled_quantity == 25 and leg.status == "PARTIALLY_FILLED"
    assert intent.status == IntentStatus.PARTIALLY_FILLED.value


def test_tradebook_sync_replay_is_idempotent(db):
    _working_intent(db)
    adapter = _adapter(_snapshot())
    first = sync.sync_account_tradebook_fills(db, adapter=adapter, account_id="acct-1")
    second = sync.sync_account_tradebook_fills(db, adapter=adapter, account_id="acct-1")
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "ce-buy"))
    assert first["created_count"] == 1
    assert second["created_count"] == 0
    assert second["duplicate_count"] == 1
    assert leg.filled_quantity == 25
    assert db.query(ExecutionFillRecord).count() == 1


@pytest.mark.parametrize(
    "snapshot,reason",
    [
        (_snapshot(complete=False), "BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH"),
        (_snapshot(broker="zerodha"), "BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH"),
        (_snapshot(account="other"), "BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH"),
        (_snapshot(trades=[BrokerTradeSnapshot("trade-1", "order-ce", 25, None, 1_800_000_000_000_000_000)]), "TRADE_EXECUTION_PRICE_OR_TIMESTAMP_MISSING"),
        (_snapshot(trades=[BrokerTradeSnapshot("trade-1", "order-ce", 25, 101.25, None)]), "TRADE_EXECUTION_PRICE_OR_TIMESTAMP_MISSING"),
        (_snapshot(trades=[BrokerTradeSnapshot("trade-1", "order-ce", 20, 101.25, 1_800_000_000_000_000_000)]), "ORDER_TRADE_FILL_TOTAL_MISMATCH"),
        (_snapshot(trades=[BrokerTradeSnapshot("trade-1", "unknown-order", 25, 101.25, 1_800_000_000_000_000_000)]), "TRADE_REFERENCES_UNKNOWN_BROKER_ORDER"),
    ],
)
def test_tradebook_sync_fails_closed_before_any_fill_write(db, snapshot, reason):
    _working_intent(db)
    result = sync.sync_account_tradebook_fills(
        db, adapter=_adapter(snapshot), account_id="acct-1",
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == reason
    assert db.query(ExecutionFillRecord).count() == 0
    leg = db.scalar(select(ExecutionLegRecord).where(ExecutionLegRecord.leg_id == "ce-buy"))
    assert leg.filled_quantity == 0


def test_tradebook_sync_blocks_unregistered_order_without_guessing_leg(db):
    _working_intent(db, order_count=0)
    result = sync.sync_account_tradebook_fills(
        db, adapter=_adapter(_snapshot()), account_id="acct-1",
    )
    assert result["reason"] == "UNREGISTERED_BROKER_ORDER"
    assert db.query(ExecutionFillRecord).count() == 0


def test_tradebook_sync_blocks_disconnected_or_wrong_account_session(db):
    _working_intent(db)
    assert sync.sync_account_tradebook_fills(
        db, adapter=_adapter(_snapshot(), connected=False), account_id="acct-1",
    )["reason"] == "BROKER_SESSION_NOT_CONNECTED"
    assert sync.sync_account_tradebook_fills(
        db, adapter=_adapter(_snapshot(), account_id="other"), account_id="acct-1",
    )["reason"] == "BROKER_SESSION_ACCOUNT_MISMATCH"
    assert db.query(ExecutionFillRecord).count() == 0


def test_tradebook_batch_rolls_back_when_a_later_fill_write_fails(db, monkeypatch):
    _working_intent(db, order_count=2)
    snapshot = BrokerSnapshot(
        "acct-1", "angel_one",
        (
            BrokerOrderSnapshot("order-ce", "NFO:CE", "BUY", 50, 25, "PARTIALLY_FILLED"),
            BrokerOrderSnapshot("order-cs", "NFO:CE-HIGH", "SELL", 50, 25, "PARTIALLY_FILLED"),
        ),
        (
            BrokerTradeSnapshot("trade-ce", "order-ce", 25, 101.25, 1_800_000_000_000_000_000),
            BrokerTradeSnapshot("trade-cs", "order-cs", 25, 99.5, 1_800_000_000_000_000_001),
        ),
        True,
    )
    original = sync.record_fill
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 2:
            raise RuntimeError("simulated later fill failure")
        return result

    monkeypatch.setattr(sync, "record_fill", fail_second)
    with pytest.raises(RuntimeError, match="simulated later fill failure"):
        sync.sync_account_tradebook_fills(
            db, adapter=_adapter(snapshot), account_id="acct-1",
        )
    assert db.query(ExecutionFillRecord).count() == 0
    legs = db.scalars(select(ExecutionLegRecord).order_by(ExecutionLegRecord.leg_id)).all()
    assert all(leg.filled_quantity == 0 for leg in legs)


def test_unmanaged_manual_trades_are_reported_but_never_adopted(db):
    _working_intent(db)
    snapshot = BrokerSnapshot(
        "acct-1", "angel_one",
        (
            BrokerOrderSnapshot("order-ce", "NFO:CE", "BUY", 50, 25, "PARTIALLY_FILLED"),
            BrokerOrderSnapshot("manual-order", "NFO:MANUAL", "BUY", 10, 5, "PARTIALLY_FILLED"),
        ),
        (
            BrokerTradeSnapshot("trade-1", "order-ce", 25, 101.25, 1_800_000_000_000_000_000),
            BrokerTradeSnapshot("manual-trade-1", "manual-order", 5, None, None),
        ),
        True,
    )
    result = sync.sync_account_tradebook_fills(
        db, adapter=_adapter(snapshot), account_id="acct-1",
    )
    assert result["status"] == "SYNCED_WITH_UNMATCHED"
    assert result["managed_trade_count"] == 1
    assert result["created_count"] == 1
    assert result["unmatched_trade_count"] == 1
    assert result["unmatched_trade_ids"] == ["manual-trade-1"]
    assert db.query(ExecutionFillRecord).count() == 1
    assert db.scalar(select(ExecutionFillRecord)).broker_fill_id == "trade-1"
