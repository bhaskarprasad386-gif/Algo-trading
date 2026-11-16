from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.brokers.history import BrokerHistoryUnavailable
from app.brokers.shoonya import ShoonyaAdapter
from app.brokers.shoonya_history import ShoonyaHistoryProvider


IST = ZoneInfo("Asia/Kolkata")


class FakeShoonya:
    def __init__(self):
        self.calls = []

    def get_user_details(self):
        self.calls.append("profile")
        return {"stat": "Ok", "uid": "UID-1", "actid": "SH-1"}

    def get_order_book(self):
        self.calls.append("orders")
        return []

    def get_trade_book(self):
        self.calls.append("trades")
        return []


def _provider(fake):
    adapter = ShoonyaAdapter(fake)
    adapter.connect(uid="UID-1", account_id="SH-1")
    return ShoonyaHistoryProvider(adapter)


def _window():
    start = datetime.combine(datetime.now(IST).date(), time.min, tzinfo=IST)
    return start, start + timedelta(days=1)


def test_shoonya_provider_returns_complete_current_day_snapshot():
    fake = FakeShoonya()
    start, end = _window()
    result = _provider(fake).fetch_order_trade_snapshot(
        account_id="SH-1", window_start=start, window_end=end,
    )
    assert result.complete is True
    assert result.provider_window == "CURRENT_TRADING_DAY_ORDER_BOOK_AND_TRADE_BOOK"
    assert result.pages_fetched == 1
    assert result.snapshot.account_id == "SH-1"
    assert result.snapshot.broker == "shoonya"
    assert fake.calls == ["profile", "orders", "trades"]


def test_shoonya_provider_rejects_multi_day_window_before_api_calls():
    fake = FakeShoonya()
    start, end = _window()
    with pytest.raises(BrokerHistoryUnavailable, match="complete current IST"):
        _provider(fake).fetch_order_trade_snapshot(
            account_id="SH-1", window_start=start - timedelta(days=1), window_end=end,
        )
    assert fake.calls == ["profile"]


def test_shoonya_provider_rejects_partial_day_window_before_api_calls():
    fake = FakeShoonya()
    start, end = _window()
    with pytest.raises(BrokerHistoryUnavailable, match="complete current IST"):
        _provider(fake).fetch_order_trade_snapshot(
            account_id="SH-1", window_start=start + timedelta(minutes=1), window_end=end,
        )
    assert fake.calls == ["profile"]


def test_shoonya_provider_rejects_naive_timestamps():
    fake = FakeShoonya()
    start, end = _window()
    with pytest.raises(BrokerHistoryUnavailable, match="timezone-aware"):
        _provider(fake).fetch_order_trade_snapshot(
            account_id="SH-1", window_start=start.replace(tzinfo=None), window_end=end,
        )
    assert fake.calls == ["profile"]


def test_shoonya_provider_rejects_wrong_account_before_book_calls():
    fake = FakeShoonya()
    start, end = _window()
    with pytest.raises(BrokerHistoryUnavailable, match="account scope"):
        _provider(fake).fetch_order_trade_snapshot(
            account_id="OTHER", window_start=start, window_end=end,
        )
    assert fake.calls == ["profile"]


def test_shoonya_provider_does_not_return_partial_snapshot_on_trade_failure():
    class BrokenTradeBook(FakeShoonya):
        def get_trade_book(self):
            self.calls.append("trades")
            raise RuntimeError("temporary failure")

    fake = BrokenTradeBook()
    start, end = _window()
    with pytest.raises(BrokerHistoryUnavailable, match="request failed"):
        _provider(fake).fetch_order_trade_snapshot(
            account_id="SH-1", window_start=start, window_end=end,
        )
