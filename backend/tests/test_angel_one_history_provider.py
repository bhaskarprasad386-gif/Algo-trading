from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.brokers.angel_one import AngelOneAdapter, BrokerHistoryUnavailable
from app.brokers.angel_one_history import AngelOneHistoryProvider


IST = ZoneInfo("Asia/Kolkata")


class FakeSmartAPI:
    def __init__(self, orders=None, trades=None):
        self.orders = orders if orders is not None else {"status": True, "data": []}
        self.trades = trades if trades is not None else {"status": True, "data": []}
        self.calls = []

    def orderBook(self):
        self.calls.append("orderBook")
        return self.orders

    def tradeBook(self):
        self.calls.append("tradeBook")
        return self.trades


def _provider(fake):
    adapter = AngelOneAdapter()
    adapter.smart_api = fake
    adapter.client_code = "ANGEL-CLIENT"
    adapter.account_id = "acct-1"
    adapter.connected = True
    return AngelOneHistoryProvider(adapter)


def _today_window():
    start = datetime.combine(datetime.now(IST).date(), datetime.min.time(), tzinfo=IST)
    return start, start + timedelta(days=1)


def test_angel_one_provider_returns_complete_current_day_contract():
    fake = FakeSmartAPI(
        orders={"status": True, "data": [{
            "orderid": "AO-1", "tradingsymbol": "NIFTY26NOV25000CE",
            "transactiontype": "BUY", "quantity": "50", "filledshares": "25",
        }]},
        trades={"status": True, "data": [{
            "tradeid": "AT-1", "orderid": "AO-1", "fillshares": "25",
        }]},
    )
    start, end = _today_window()
    result = _provider(fake).fetch_order_trade_snapshot(
        account_id="acct-1", window_start=start, window_end=end,
    )
    assert result.complete is True
    assert result.provider_window == "CURRENT_TRADING_DAY"
    assert result.pages_fetched == 1
    assert result.snapshot.account_id == "acct-1"
    assert result.snapshot.orders[0].broker_order_id == "AO-1"
    assert result.snapshot.trades[0].broker_trade_id == "AT-1"
    assert fake.calls == ["orderBook", "tradeBook"]


def test_angel_one_provider_rejects_multi_day_history_request():
    fake = FakeSmartAPI()
    start, end = _today_window()
    with pytest.raises(BrokerHistoryUnavailable, match="complete current IST"):
        _provider(fake).fetch_order_trade_snapshot(
            account_id="acct-1", window_start=start - timedelta(days=1), window_end=end,
        )
    assert fake.calls == []


def test_angel_one_provider_rejects_partial_day_history_request():
    fake = FakeSmartAPI()
    start, end = _today_window()
    with pytest.raises(BrokerHistoryUnavailable, match="complete current IST"):
        _provider(fake).fetch_order_trade_snapshot(
            account_id="acct-1", window_start=start + timedelta(hours=1), window_end=end,
        )
    assert fake.calls == []


def test_angel_one_provider_rejects_naive_window():
    fake = FakeSmartAPI()
    start, end = _today_window()
    with pytest.raises(BrokerHistoryUnavailable, match="timezone-aware"):
        _provider(fake).fetch_order_trade_snapshot(
            account_id="acct-1", window_start=start.replace(tzinfo=None), window_end=end,
        )
    assert fake.calls == []


def test_angel_one_provider_keeps_account_scope_fail_closed():
    fake = FakeSmartAPI()
    start, end = _today_window()
    with pytest.raises(BrokerHistoryUnavailable, match="account scope"):
        _provider(fake).fetch_order_trade_snapshot(
            account_id="acct-2", window_start=start, window_end=end,
        )
    assert fake.calls == ["orderBook", "tradeBook"]


def test_angel_one_provider_never_calls_order_mutation_endpoints():
    fake = FakeSmartAPI()
    start, end = _today_window()
    _provider(fake).fetch_order_trade_snapshot(
        account_id="acct-1", window_start=start, window_end=end,
    )
    assert fake.calls == ["orderBook", "tradeBook"]
