from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.broker_order_event import BrokerOrderEventRecord
from app.execution import broker_snapshot_event_sync as sync
from app.execution.broker_events import BrokerOrderEventStatus


def _order(**changes):
    values = dict(
        broker_order_id="order-1", symbol="NIFTY26NOV25000CE",
        side="BUY", quantity=50, filled_quantity=0, raw_status="OPEN",
    )
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "raw,filled,expected",
    [
        ("open", 0, BrokerOrderEventStatus.OPEN),
        ("COMPLETE", 50, BrokerOrderEventStatus.FILLED),
        ("open", 10, BrokerOrderEventStatus.PARTIALLY_FILLED),
        ("cancelled", 10, BrokerOrderEventStatus.CANCELLED),
        ("rejected", 0, BrokerOrderEventStatus.REJECTED),
        ("vendor-new-status", 0, BrokerOrderEventStatus.UNKNOWN),
        ("COMPLETE", 0, BrokerOrderEventStatus.UNKNOWN),
    ],
)
def test_snapshot_order_status_is_normalized_without_fabricating_fill(raw, filled, expected):
    event = sync.snapshot_order_to_event(
        broker="zerodha", account_id="acct-1",
        order=_order(raw_status=raw, filled_quantity=filled),
    )
    assert event.status is expected
    assert event.fill_id is None
    assert event.fill_quantity is None
    assert event.average_fill_price is None
    assert event.occurred_at_ns == 0


def test_snapshot_order_event_identity_is_stable_and_changes_with_state():
    first = sync.snapshot_order_to_event(
        broker="fyers", account_id="acct-1", order=_order(raw_status="OPEN"),
    )
    replay = sync.snapshot_order_to_event(
        broker="fyers", account_id="acct-1", order=_order(raw_status="OPEN"),
    )
    updated = sync.snapshot_order_to_event(
        broker="fyers", account_id="acct-1",
        order=_order(raw_status="OPEN", filled_quantity=10),
    )
    assert first.event_id == replay.event_id
    assert first.event_id != updated.event_id


@pytest.mark.parametrize(
    "changes",
    [
        {"side": "UNKNOWN"},
        {"quantity": 0},
        {"filled_quantity": -1},
        {"filled_quantity": 51},
        {"quantity": True},
    ],
)
def test_invalid_snapshot_order_is_rejected(changes):
    with pytest.raises(ValueError):
        sync.snapshot_order_to_event(
            broker="mstock", account_id="acct-1", order=_order(**changes),
        )


def _adapter(history, *, name="angel_one", account_id="acct-1", connected=True):
    return SimpleNamespace(
        name=name, account_id=account_id, connected=connected,
        fetch_today_broker_snapshot=lambda **kwargs: history,
    )


def _history(*, complete=True, account_id="acct-1", broker="angel_one", scope="CURRENT_TRADING_DAY", orders=None):
    snapshot = SimpleNamespace(
        complete=complete, account_id=account_id, broker=broker,
        orders=tuple(orders or ()), trades=(),
    )
    return SimpleNamespace(scope=scope, snapshot=snapshot, trading_date="2026-11-16")


@pytest.mark.parametrize("broker", ["angel_one", "zerodha", "fyers", "shoonya", "mstock"])
def test_sync_persists_all_order_events_and_reports_counts(monkeypatch, broker):
    seen = []
    monkeypatch.setattr(
        sync, "record_broker_order_event",
        lambda db, event: seen.append(event) or {"created": len(seen) == 1},
    )
    result = sync.sync_account_order_status_events(
        object(), adapter=_adapter(_history(broker=broker, orders=[_order(), _order(
            broker_order_id="order-2", raw_status="COMPLETE", filled_quantity=50,
        )]), name=broker), account_id="acct-1",
    )
    assert result["status"] == "SYNCED"
    assert result["event_count"] == 2
    assert result["created_count"] == 1
    assert result["duplicate_count"] == 1
    assert len(seen) == 2
    assert all(event.broker == broker for event in seen)


@pytest.mark.parametrize(
    "history,expected",
    [
        (_history(complete=False), "BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH"),
        (_history(account_id="other"), "BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH"),
        (_history(broker="fyers"), "BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH"),
        (_history(scope="UNKNOWN"), "BROKER_HISTORY_SCOPE_UNSUPPORTED"),
    ],
)
def test_sync_blocks_bad_snapshot_before_any_write(monkeypatch, history, expected):
    monkeypatch.setattr(
        sync, "record_broker_order_event",
        lambda *args, **kwargs: pytest.fail("must not persist an invalid snapshot"),
    )
    result = sync.sync_account_order_status_events(
        object(), adapter=_adapter(history), account_id="acct-1",
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == expected
    assert result["event_count"] == 0


def test_sync_blocks_disconnected_or_wrong_account_session(monkeypatch):
    history = _history(orders=[_order()])
    monkeypatch.setattr(
        sync, "record_broker_order_event",
        lambda *args, **kwargs: pytest.fail("must not persist"),
    )
    disconnected = sync.sync_account_order_status_events(
        object(), adapter=_adapter(history, connected=False), account_id="acct-1",
    )
    wrong_account = sync.sync_account_order_status_events(
        object(), adapter=_adapter(history, account_id="acct-2"), account_id="acct-1",
    )
    assert disconnected["reason"] == "BROKER_SESSION_NOT_CONNECTED"
    assert wrong_account["reason"] == "BROKER_SESSION_ACCOUNT_MISMATCH"



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


def test_snapshot_batch_rolls_back_all_events_if_any_persist_fails(db, monkeypatch):
    original = sync.record_broker_order_event
    calls = 0

    def fail_on_second(db_session, event):
        nonlocal calls
        calls += 1
        result = original(db_session, event)
        if calls == 2:
            raise RuntimeError("simulated persistence failure")
        return result

    monkeypatch.setattr(sync, "record_broker_order_event", fail_on_second)
    adapter = _adapter(_history(orders=[
        _order(broker_order_id="order-a"),
        _order(broker_order_id="order-b"),
    ]))
    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        sync.sync_account_order_status_events(db, adapter=adapter, account_id="acct-1")
    assert db.scalar(select(BrokerOrderEventRecord)) is None


from app.execution.broker_reconciliation import (
    BrokerTradeSnapshot,
    parse_positive_trade_price,
    parse_trade_timestamp_ns,
)


@pytest.mark.parametrize("value", ["", None, "01-01-1980 00:00:00", "not-a-time"])
def test_untrusted_or_placeholder_trade_timestamp_is_not_fabricated(value):
    assert parse_trade_timestamp_ns(value, trading_date="2026-11-16") is None


def test_time_only_trade_timestamp_is_anchored_to_explicit_ist_trading_date():
    timestamp = parse_trade_timestamp_ns("10:31:00", trading_date="2026-11-16")
    assert timestamp is not None and timestamp > 0


@pytest.mark.parametrize("price", [None, "", "bad", 0, -1, float("nan"), float("inf")])
def test_untrusted_provider_fill_price_remains_unknown(price):
    assert parse_positive_trade_price(price) is None


def test_valid_provider_fill_price_is_preserved():
    assert parse_positive_trade_price("101.25") == 101.25


def test_normalized_trade_snapshot_rejects_invalid_optional_execution_evidence():
    with pytest.raises(ValueError, match="fill price"):
        BrokerTradeSnapshot("trade-1", "order-1", 10, fill_price=0)
    with pytest.raises(ValueError, match="timestamp"):
        BrokerTradeSnapshot("trade-1", "order-1", 10, fill_price=100, occurred_at_ns=0)
