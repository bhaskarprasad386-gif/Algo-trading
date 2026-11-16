from types import SimpleNamespace

import pytest

from app.brokers.angel_one import AngelOneAdapter, BrokerHistoryUnavailable


class FakeSmartAPI:
    def __init__(self, orders=None, trades=None):
        self.orders = orders if orders is not None else []
        self.trades = trades if trades is not None else []
        self.calls = []

    def orderBook(self):
        self.calls.append("orderBook")
        return self.orders

    def tradeBook(self):
        self.calls.append("tradeBook")
        return self.trades


def connected_adapter(fake, account_id="account-123"):
    adapter = AngelOneAdapter()
    adapter.smart_api = fake
    adapter.client_code = "ANGEL-CLIENT"
    adapter.account_id = account_id
    adapter.connected = True
    return adapter


def test_fetch_today_snapshot_normalizes_complete_order_and_trade_books():
    fake = FakeSmartAPI(
        orders={"status": True, "data": [{
            "orderid": "AO-1", "tradingsymbol": "NIFTY26NOV25000CE",
            "transactiontype": "BUY", "quantity": "50", "filledshares": "25",
        }]},
        trades={"status": True, "data": [{
            "tradeid": "AT-1", "orderid": "AO-1", "fillshares": "25",
        }]},
    )
    result = connected_adapter(fake).fetch_today_broker_snapshot(account_id="account-123")

    assert result.scope == "CURRENT_TRADING_DAY"
    assert result.trading_date
    assert result.snapshot.complete is True
    assert result.snapshot.account_id == "account-123"
    assert result.snapshot.broker == "angel_one"
    assert result.snapshot.orders[0].broker_order_id == "AO-1"
    assert result.snapshot.orders[0].filled_quantity == 25
    assert result.snapshot.trades[0].broker_trade_id == "AT-1"
    assert result.snapshot.trades[0].quantity == 25
    assert fake.calls == ["orderBook", "tradeBook"]


def test_empty_successful_books_are_a_complete_current_day_snapshot():
    fake = FakeSmartAPI(
        orders={"status": True, "data": []},
        trades={"status": True, "data": []},
    )
    result = connected_adapter(fake).fetch_today_broker_snapshot(account_id="account-123")
    assert result.snapshot.complete is True
    assert result.snapshot.orders == ()
    assert result.snapshot.trades == ()


@pytest.mark.parametrize("response", [
    None,
    {"status": False, "message": "session expired", "data": []},
    {"status": True, "data": None},
    {"status": True, "data": {}},
    {"status": True, "data": ["not-a-row"]},
])
def test_invalid_or_unsuccessful_order_book_never_becomes_complete(response):
    fake = FakeSmartAPI(orders=response, trades={"status": True, "data": []})
    with pytest.raises(BrokerHistoryUnavailable):
        connected_adapter(fake).fetch_today_broker_snapshot(account_id="account-123")


def test_trade_book_failure_never_returns_partial_complete_snapshot():
    fake = FakeSmartAPI(
        orders={"status": True, "data": []},
        trades={"status": False, "message": "temporary failure", "data": None},
    )
    with pytest.raises(BrokerHistoryUnavailable, match="tradeBook"):
        connected_adapter(fake).fetch_today_broker_snapshot(account_id="account-123")


def test_history_is_rejected_when_session_is_disconnected_or_account_scope_differs():
    fake = FakeSmartAPI(
        orders={"status": True, "data": []},
        trades={"status": True, "data": []},
    )
    adapter = connected_adapter(fake)
    with pytest.raises(BrokerHistoryUnavailable, match="account scope"):
        adapter.fetch_today_broker_snapshot(account_id="another-account")

    adapter.connected = False
    with pytest.raises(BrokerHistoryUnavailable, match="not connected"):
        adapter.fetch_today_broker_snapshot(account_id="account-123")


@pytest.mark.parametrize("row", [
    {"orderid": "AO-1", "transactiontype": "BUY", "quantity": "10", "filledshares": "0"},
    {"orderid": "AO-1", "tradingsymbol": "ABC", "transactiontype": "HOLD", "quantity": "10", "filledshares": "0"},
    {"orderid": "AO-1", "tradingsymbol": "ABC", "transactiontype": "BUY", "quantity": "0", "filledshares": "0"},
    {"orderid": "AO-1", "tradingsymbol": "ABC", "transactiontype": "BUY", "quantity": "10", "filledshares": "11"},
])
def test_malformed_order_rows_are_rejected_not_silently_dropped(row):
    fake = FakeSmartAPI(
        orders={"status": True, "data": [row]},
        trades={"status": True, "data": []},
    )
    with pytest.raises((BrokerHistoryUnavailable, ValueError)):
        connected_adapter(fake).fetch_today_broker_snapshot(account_id="account-123")


def test_malformed_trade_row_is_rejected():
    fake = FakeSmartAPI(
        orders={"status": True, "data": []},
        trades={"status": True, "data": [{
            "tradeid": "AT-1", "orderid": "AO-1", "fillshares": "0",
        }]},
    )
    with pytest.raises(BrokerHistoryUnavailable):
        connected_adapter(fake).fetch_today_broker_snapshot(account_id="account-123")


def test_history_adapter_does_not_call_order_mutation_methods():
    fake = FakeSmartAPI(
        orders={"status": True, "data": []},
        trades={"status": True, "data": []},
    )
    adapter = connected_adapter(fake)
    adapter.fetch_today_broker_snapshot(account_id="account-123")
    assert fake.calls == ["orderBook", "tradeBook"]
    assert not hasattr(fake, "placeOrder")
    assert not hasattr(fake, "cancelOrder")
