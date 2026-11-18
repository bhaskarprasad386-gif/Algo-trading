from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.brokers.history import BrokerHistoryUnavailable
from app.brokers.zerodha import ZerodhaAdapter
from app.brokers.zerodha_history import ZerodhaHistoryProvider


IST = ZoneInfo("Asia/Kolkata")


class FakeKite:
    def __init__(self):
        self.calls = []
        self.token = None

    def set_access_token(self, token):
        self.token = token
        self.calls.append(("set_access_token", token))

    def profile(self):
        self.calls.append(("profile",))
        return {"user_id": "ZER-123"}

    def orders(self):
        self.calls.append(("orders",))
        return [{
            "order_id": "ZO-1", "tradingsymbol": "NIFTY26NOV25000CE",
            "transaction_type": "BUY", "quantity": 50, "filled_quantity": 25,
        }]

    def trades(self):
        self.calls.append(("trades",))
        return [{"trade_id": "ZT-1", "order_id": "ZO-1", "quantity": 25, "average_price": 101.25, "fill_timestamp": "2026-11-16 10:31:00"}]


def _connected():
    kite = FakeKite()
    adapter = ZerodhaAdapter(kite)
    adapter.connect(access_token="test-token", account_id="ZER-123")
    return kite, ZerodhaHistoryProvider(adapter)


def _today_window():
    start = datetime.combine(datetime.now(IST).date(), datetime.min.time(), tzinfo=IST)
    return start, start + timedelta(days=1)


def test_zerodha_provider_returns_complete_current_day_contract():
    kite, provider = _connected()
    start, end = _today_window()
    result = provider.fetch_order_trade_snapshot(
        account_id="ZER-123", window_start=start, window_end=end,
    )
    assert result.complete is True
    assert result.provider_window == "CURRENT_TRADING_DAY"
    assert result.pages_fetched == 1
    assert result.snapshot.account_id == "ZER-123"
    assert result.snapshot.broker == "zerodha"
    assert result.snapshot.orders[0].broker_order_id == "ZO-1"
    assert result.snapshot.trades[0].broker_trade_id == "ZT-1"
    assert [call[0] for call in kite.calls if call[0] in {"orders", "trades"}] == ["orders", "trades"]


def test_zerodha_provider_rejects_multi_day_request_before_api_calls():
    kite, provider = _connected()
    start, end = _today_window()
    with pytest.raises(BrokerHistoryUnavailable, match="complete current IST"):
        provider.fetch_order_trade_snapshot(
            account_id="ZER-123", window_start=start - timedelta(days=1), window_end=end,
        )
    assert [call[0] for call in kite.calls if call[0] in {"orders", "trades"}] == []


def test_zerodha_provider_rejects_partial_day_request_before_api_calls():
    kite, provider = _connected()
    start, end = _today_window()
    with pytest.raises(BrokerHistoryUnavailable, match="complete current IST"):
        provider.fetch_order_trade_snapshot(
            account_id="ZER-123", window_start=start + timedelta(hours=1), window_end=end,
        )
    assert [call[0] for call in kite.calls if call[0] in {"orders", "trades"}] == []


def test_zerodha_provider_rejects_naive_timestamps():
    _, provider = _connected()
    start, end = _today_window()
    with pytest.raises(BrokerHistoryUnavailable, match="timezone-aware"):
        provider.fetch_order_trade_snapshot(
            account_id="ZER-123", window_start=start.replace(tzinfo=None), window_end=end,
        )


def test_zerodha_provider_preserves_account_scope_guard():
    _, provider = _connected()
    start, end = _today_window()
    with pytest.raises(Exception, match="account scope"):
        provider.fetch_order_trade_snapshot(
            account_id="OTHER", window_start=start, window_end=end,
        )
