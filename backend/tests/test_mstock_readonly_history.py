import pytest

from app.brokers.mstock import MStockAdapter, MStockHistoryUnavailable


class FakeResponse:
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


class FakeHTTP:
    def __init__(self, orders=None, trades=None):
        self.responses = {
            "orders": orders if orders is not None else {"status": "success", "data": []},
            "tradebook": trades if trades is not None else {"status": "success", "data": []},
        }
        self.calls = []

    def get(self, url, *, headers, timeout):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append((endpoint, headers, timeout))
        return FakeResponse(self.responses[endpoint])


def connected_adapter(http):
    adapter = MStockAdapter(http=http)
    adapter.connect(api_key="api-key", access_token="short-lived-token", account_id="mstock-account")
    return adapter


def test_mstock_normalizes_order_and_trade_books_read_only():
    http = FakeHTTP(
        orders={"status": "success", "data": [{
            "order_id": "MO-1", "tradingsymbol": "NIFTY26NOV25000CE",
            "transaction_type": "BUY", "quantity": 50, "filled_quantity": 25,
        }]},
        trades={"status": "success", "data": [{
            "TRADE_NUMBER": "MT-1", "ORDER_NUMBER": "MO-1",
            "QUANTITY": 25, "BUY_SELL": "Buy",
        }]},
    )
    result = connected_adapter(http).fetch_today_broker_snapshot(account_id="mstock-account")

    assert result.scope == "CURRENT_TRADING_DAY"
    assert result.snapshot.complete is True
    assert result.snapshot.broker == "mstock"
    assert result.snapshot.account_id == "mstock-account"
    assert result.snapshot.orders[0].broker_order_id == "MO-1"
    assert result.snapshot.orders[0].filled_quantity == 25
    assert result.snapshot.trades[0].broker_trade_id == "MT-1"
    assert result.snapshot.trades[0].quantity == 25
    assert [call[0] for call in http.calls] == ["orders", "tradebook"]
    assert all(call[1]["Authorization"] == "token api-key:short-lived-token" for call in http.calls)


def test_explicit_empty_successful_books_are_complete():
    result = connected_adapter(FakeHTTP()).fetch_today_broker_snapshot(account_id="mstock-account")
    assert result.snapshot.complete is True
    assert result.snapshot.orders == ()
    assert result.snapshot.trades == ()


@pytest.mark.parametrize("payload", [
    None,
    {"status": "error", "data": []},
    {"status": "success", "data": None},
    {"status": "success", "data": {}},
    {"status": "success", "data": ["not-a-row"]},
])
def test_invalid_order_book_never_becomes_complete(payload):
    http = FakeHTTP(orders=payload)
    with pytest.raises(MStockHistoryUnavailable):
        connected_adapter(http).fetch_today_broker_snapshot(account_id="mstock-account")


def test_trade_book_failure_does_not_return_partial_snapshot():
    http = FakeHTTP(trades={"status": "error", "data": None})
    with pytest.raises(MStockHistoryUnavailable, match="tradebook"):
        connected_adapter(http).fetch_today_broker_snapshot(account_id="mstock-account")


def test_account_scope_and_session_are_required():
    adapter = connected_adapter(FakeHTTP())
    with pytest.raises(MStockHistoryUnavailable, match="account scope"):
        adapter.fetch_today_broker_snapshot(account_id="different-account")
    adapter.disconnect()
    with pytest.raises(MStockHistoryUnavailable, match="not connected"):
        adapter.fetch_today_broker_snapshot(account_id="mstock-account")


@pytest.mark.parametrize("row", [
    {"order_id": "MO-1", "tradingsymbol": "ABC", "transaction_type": "HOLD", "quantity": 10, "filled_quantity": 0},
    {"order_id": "MO-1", "tradingsymbol": "ABC", "transaction_type": "BUY", "quantity": 0, "filled_quantity": 0},
    {"order_id": "MO-1", "tradingsymbol": "ABC", "transaction_type": "BUY", "quantity": 10, "filled_quantity": 11},
])
def test_malformed_order_rows_are_rejected(row):
    http = FakeHTTP(orders={"status": "success", "data": [row]})
    with pytest.raises((MStockHistoryUnavailable, ValueError)):
        connected_adapter(http).fetch_today_broker_snapshot(account_id="mstock-account")


def test_malformed_trade_row_is_rejected():
    http = FakeHTTP(trades={"status": "success", "data": [{
        "TRADE_NUMBER": "MT-1", "ORDER_NUMBER": "MO-1", "QUANTITY": 0,
    }]})
    with pytest.raises(MStockHistoryUnavailable):
        connected_adapter(http).fetch_today_broker_snapshot(account_id="mstock-account")


def test_mutating_order_methods_remain_disabled():
    adapter = connected_adapter(FakeHTTP())
    with pytest.raises(NotImplementedError):
        adapter.place_order()
    with pytest.raises(NotImplementedError):
        adapter.cancel_order()
