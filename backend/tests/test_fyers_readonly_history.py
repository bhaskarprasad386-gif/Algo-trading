import pytest

from app.brokers.fyers import FyersAdapter, FyersHistoryUnavailable


_DEFAULT = object()


class FakeFyers:
    def __init__(self, profile=_DEFAULT, orders=_DEFAULT, trades=_DEFAULT,
                 profile_error=None, order_error=None, trade_error=None):
        self.profile_data = {
            "s": "ok", "code": 200, "data": {"fy_id": "FY-123"}
        } if profile is _DEFAULT else profile
        self.order_rows = {"s": "ok", "code": 200, "orderBook": []} if orders is _DEFAULT else orders
        self.trade_rows = {"s": "ok", "code": 200, "tradeBook": []} if trades is _DEFAULT else trades
        self.profile_error = profile_error
        self.order_error = order_error
        self.trade_error = trade_error
        self.calls = []

    def get_profile(self):
        self.calls.append("profile")
        if self.profile_error:
            raise self.profile_error
        return self.profile_data

    def orderbook(self):
        self.calls.append("orders")
        if self.order_error:
            raise self.order_error
        return self.order_rows

    def tradebook(self):
        self.calls.append("trades")
        if self.trade_error:
            raise self.trade_error
        return self.trade_rows


def connected_adapter(client):
    adapter = FyersAdapter(client)
    result = adapter.connect(account_id="FY-123")
    assert result == {"broker": "fyers", "connected": True, "account_id": "FY-123"}
    return adapter


def test_normalizes_complete_order_and_trade_books_read_only():
    client = FakeFyers(
        orders={"s": "ok", "code": 200, "orderBook": [{
            "id": "FO-1", "symbol": "NSE:NIFTY26NOV25000CE",
            "side": 1, "qty": 50, "filledQty": 25, "status": 8,
        }]},
        trades={"s": "ok", "code": 200, "tradeBook": [{
            "tradeNumber": "FT-1", "orderNumber": "FO-1", "tradedQty": 25,
        }]},
    )
    result = connected_adapter(client).fetch_today_broker_snapshot(account_id="FY-123")
    assert result.scope == "CURRENT_TRADING_DAY"
    assert result.trading_date
    assert result.snapshot.complete is True
    assert result.snapshot.account_id == "FY-123"
    assert result.snapshot.broker == "fyers"
    assert result.snapshot.orders[0].broker_order_id == "FO-1"
    assert result.snapshot.orders[0].side == "BUY"
    assert result.snapshot.orders[0].filled_quantity == 25
    assert result.snapshot.orders[0].raw_status == "8"
    assert result.snapshot.trades[0].broker_trade_id == "FT-1"
    assert result.snapshot.trades[0].quantity == 25
    assert client.calls == ["profile", "orders", "trades"]


def test_successful_empty_books_are_complete():
    result = connected_adapter(FakeFyers()).fetch_today_broker_snapshot(account_id="FY-123")
    assert result.snapshot.complete is True
    assert result.snapshot.orders == ()
    assert result.snapshot.trades == ()


@pytest.mark.parametrize("profile", [
    None, [], {"s": "error", "code": -8},
    {"s": "ok", "code": 200, "data": {}},
])
def test_invalid_profile_fails_closed(profile):
    adapter = FyersAdapter(FakeFyers(profile=profile))
    with pytest.raises(FyersHistoryUnavailable):
        adapter.connect(account_id="FY-123")
    assert adapter.connected is False


def test_account_mismatch_fails_closed():
    adapter = FyersAdapter(FakeFyers(profile={
        "s": "ok", "code": 200, "data": {"fy_id": "OTHER"},
    }))
    with pytest.raises(FyersHistoryUnavailable, match="does not match"):
        adapter.connect(account_id="FY-123")
    assert adapter.connected is False


@pytest.mark.parametrize("response", [
    None, {"s": "error", "code": -8}, {"s": "ok", "code": 200, "orderBook": None},
    {"s": "ok", "code": 200, "orderBook": ["bad-row"]},
])
def test_malformed_order_book_never_becomes_complete(response):
    adapter = connected_adapter(FakeFyers(orders=response))
    with pytest.raises(FyersHistoryUnavailable):
        adapter.fetch_today_broker_snapshot(account_id="FY-123")


def test_trade_book_failure_never_returns_partial_snapshot():
    adapter = connected_adapter(FakeFyers(trades={"s": "error", "code": -8}))
    with pytest.raises(FyersHistoryUnavailable):
        adapter.fetch_today_broker_snapshot(account_id="FY-123")


def test_wrong_account_and_disconnected_session_are_rejected():
    adapter = connected_adapter(FakeFyers())
    with pytest.raises(FyersHistoryUnavailable, match="account scope"):
        adapter.fetch_today_broker_snapshot(account_id="OTHER")
    adapter.disconnect()
    with pytest.raises(FyersHistoryUnavailable, match="not connected"):
        adapter.fetch_today_broker_snapshot(account_id="FY-123")


@pytest.mark.parametrize("row", [
    {"id": "FO-1", "symbol": "ABC", "side": 0, "qty": 10, "filledQty": 0},
    {"id": "FO-1", "symbol": "ABC", "side": 1, "qty": 0, "filledQty": 0},
    {"id": "FO-1", "symbol": "ABC", "side": 1, "qty": 10, "filledQty": 11},
])
def test_malformed_order_rows_are_rejected(row):
    adapter = connected_adapter(FakeFyers(orders={
        "s": "ok", "code": 200, "orderBook": [row],
    }))
    with pytest.raises((FyersHistoryUnavailable, ValueError)):
        adapter.fetch_today_broker_snapshot(account_id="FY-123")


def test_malformed_trade_row_is_rejected():
    adapter = connected_adapter(FakeFyers(trades={
        "s": "ok", "code": 200, "tradeBook": [{
            "tradeNumber": "FT-1", "orderNumber": "FO-1", "tradedQty": 0,
        }],
    }))
    with pytest.raises(FyersHistoryUnavailable):
        adapter.fetch_today_broker_snapshot(account_id="FY-123")


def test_live_order_mutations_remain_disabled():
    adapter = connected_adapter(FakeFyers())
    with pytest.raises(NotImplementedError):
        adapter.place_order()
    with pytest.raises(NotImplementedError):
        adapter.cancel_order()
