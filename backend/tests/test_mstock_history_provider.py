from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.brokers.history import BrokerHistoryUnavailable
from app.brokers.mstock import MStockAdapter
from app.brokers.mstock_history import MStockHistoryProvider


IST = ZoneInfo("Asia/Kolkata")


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeHTTP:
    def __init__(self, fail_trade=False):
        self.calls = []
        self.fail_trade = fail_trade

    def get(self, url, *, headers, timeout):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append(endpoint)
        if endpoint == "tradebook" and self.fail_trade:
            return FakeResponse({"status": "error", "data": None})
        return FakeResponse({"status": "success", "data": []})


def _provider(http):
    adapter = MStockAdapter(http=http)
    adapter.connect(
        api_key="api-key", access_token="access-token", account_id="MS-1",
    )
    return MStockHistoryProvider(adapter)


def _window():
    start = datetime.combine(datetime.now(IST).date(), time.min, tzinfo=IST)
    return start, start + timedelta(days=1)


def test_mstock_provider_returns_complete_current_day_snapshot():
    http = FakeHTTP()
    start, end = _window()
    result = _provider(http).fetch_order_trade_snapshot(
        account_id="MS-1", window_start=start, window_end=end,
    )
    assert result.complete is True
    assert result.provider_window == "CURRENT_TRADING_DAY_ORDER_BOOK_AND_TRADE_BOOK"
    assert result.pages_fetched == 1
    assert result.snapshot.account_id == "MS-1"
    assert result.snapshot.broker == "mstock"
    assert http.calls == ["orders", "tradebook"]


def test_mstock_provider_rejects_multi_day_window_before_api_calls():
    http = FakeHTTP()
    start, end = _window()
    with pytest.raises(BrokerHistoryUnavailable, match="complete current IST"):
        _provider(http).fetch_order_trade_snapshot(
            account_id="MS-1", window_start=start - timedelta(days=1), window_end=end,
        )
    assert http.calls == []


def test_mstock_provider_rejects_partial_day_window_before_api_calls():
    http = FakeHTTP()
    start, end = _window()
    with pytest.raises(BrokerHistoryUnavailable, match="complete current IST"):
        _provider(http).fetch_order_trade_snapshot(
            account_id="MS-1", window_start=start + timedelta(minutes=1), window_end=end,
        )
    assert http.calls == []


def test_mstock_provider_rejects_naive_window():
    http = FakeHTTP()
    start, end = _window()
    with pytest.raises(BrokerHistoryUnavailable, match="timezone-aware"):
        _provider(http).fetch_order_trade_snapshot(
            account_id="MS-1", window_start=start.replace(tzinfo=None), window_end=end,
        )
    assert http.calls == []


def test_mstock_provider_rejects_wrong_account_before_history_calls():
    http = FakeHTTP()
    start, end = _window()
    with pytest.raises(BrokerHistoryUnavailable, match="account scope"):
        _provider(http).fetch_order_trade_snapshot(
            account_id="OTHER", window_start=start, window_end=end,
        )
    assert http.calls == []


def test_mstock_provider_rejects_partial_books():
    http = FakeHTTP(fail_trade=True)
    start, end = _window()
    with pytest.raises(BrokerHistoryUnavailable, match="tradebook"):
        _provider(http).fetch_order_trade_snapshot(
            account_id="MS-1", window_start=start, window_end=end,
        )
    assert http.calls == ["orders", "tradebook"]
