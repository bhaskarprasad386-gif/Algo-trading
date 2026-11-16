import pytest

from app.brokers.zerodha import ZerodhaAdapter, ZerodhaHistoryUnavailable


_DEFAULT = object()


class FakeKite:
    def __init__(self, profile=_DEFAULT, orders=_DEFAULT, trades=_DEFAULT,
                 profile_error=None, order_error=None, trade_error=None):
        self.profile_data = {"user_id": "ZER-123"} if profile is _DEFAULT else profile
        self.order_rows = [] if orders is _DEFAULT else orders
        self.trade_rows = [] if trades is _DEFAULT else trades
        self.profile_error = profile_error
        self.order_error = order_error
        self.trade_error = trade_error
        self.calls = []
        self.access_token = None

    def set_access_token(self, token):
        self.access_token = token
        self.calls.append(("set_access_token", token))

    def profile(self):
        if self.profile_error:
            raise self.profile_error
        self.calls.append(("profile",))
        return self.profile_data

    def orders(self):
        self.calls.append(("orders",))
        if self.order_error:
            raise self.order_error
        return self.order_rows

    def trades(self):
        self.calls.append(("trades",))
        if self.trade_error:
            raise self.trade_error
        return self.trade_rows


def connected_adapter(client):
    adapter = ZerodhaAdapter(client)
    result = adapter.connect(access_token="short-lived-token", account_id="ZER-123")
    assert result == {"broker": "zerodha", "connected": True, "account_id": "ZER-123"}
    return adapter


def test_zerodha_normalizes_complete_order_and_trade_books_read_only():
    client = FakeKite(
        orders=[{
            "order_id": "ZO-1", "tradingsymbol": "NIFTY26NOV25000CE",
            "transaction_type": "BUY", "quantity": 50, "filled_quantity": 25,
        }],
        trades=[{
            "trade_id": "ZT-1", "order_id": "ZO-1", "quantity": 25,
        }],
    )
    result = connected_adapter(client).fetch_today_broker_snapshot(account_id="ZER-123")

    assert result.scope == "CURRENT_TRADING_DAY"
    assert result.trading_date
    assert result.snapshot.complete is True
    assert result.snapshot.broker == "zerodha"
    assert result.snapshot.account_id == "ZER-123"
    assert result.snapshot.orders[0].broker_order_id == "ZO-1"
    assert result.snapshot.orders[0].filled_quantity == 25
    assert result.snapshot.trades[0].broker_trade_id == "ZT-1"
    assert result.snapshot.trades[0].quantity == 25
    assert [call[0] for call in client.calls if call[0] in {"orders", "trades"}] == ["orders", "trades"]


def test_successful_empty_books_are_complete():
    result = connected_adapter(FakeKite()).fetch_today_broker_snapshot(account_id="ZER-123")
    assert result.snapshot.complete is True
    assert result.snapshot.orders == ()
    assert result.snapshot.trades == ()


def test_account_identity_mismatch_fails_closed_and_clears_token():
    client = FakeKite(profile={"user_id": "OTHER"})
    adapter = ZerodhaAdapter(client)
    with pytest.raises(ZerodhaHistoryUnavailable, match="does not match"):
        adapter.connect(access_token="token", account_id="ZER-123")
    assert adapter.connected is False
    assert adapter.account_id is None
    assert client.access_token == ""


@pytest.mark.parametrize("profile", [None, [], {"user_id": ""}])
def test_invalid_profile_fails_closed(profile):
    client = FakeKite(profile=profile)
    adapter = ZerodhaAdapter(client)
    with pytest.raises(ZerodhaHistoryUnavailable):
        adapter.connect(access_token="token", account_id="ZER-123")
    assert adapter.connected is False


@pytest.mark.parametrize("rows", [None, {}, ["bad-row"]])
def test_malformed_order_book_never_becomes_complete(rows):
    client = FakeKite(orders=rows)
    adapter = connected_adapter(client)
    with pytest.raises(ZerodhaHistoryUnavailable):
        adapter.fetch_today_broker_snapshot(account_id="ZER-123")


def test_trade_book_failure_never_returns_partial_snapshot():
    client = FakeKite(trade_error=RuntimeError("temporary"))
    adapter = connected_adapter(client)
    with pytest.raises(ZerodhaHistoryUnavailable, match="request failed"):
        adapter.fetch_today_broker_snapshot(account_id="ZER-123")


def test_wrong_account_and_disconnected_session_are_rejected():
    adapter = connected_adapter(FakeKite())
    with pytest.raises(ZerodhaHistoryUnavailable, match="account scope"):
        adapter.fetch_today_broker_snapshot(account_id="OTHER")
    adapter.disconnect()
    with pytest.raises(ZerodhaHistoryUnavailable, match="not connected"):
        adapter.fetch_today_broker_snapshot(account_id="ZER-123")


@pytest.mark.parametrize("row", [
    {"order_id": "ZO-1", "tradingsymbol": "ABC", "transaction_type": "HOLD", "quantity": 10, "filled_quantity": 0},
    {"order_id": "ZO-1", "tradingsymbol": "ABC", "transaction_type": "BUY", "quantity": 0, "filled_quantity": 0},
    {"order_id": "ZO-1", "tradingsymbol": "ABC", "transaction_type": "BUY", "quantity": 10, "filled_quantity": 11},
])
def test_malformed_order_rows_are_rejected(row):
    adapter = connected_adapter(FakeKite(orders=[row]))
    with pytest.raises((ZerodhaHistoryUnavailable, ValueError)):
        adapter.fetch_today_broker_snapshot(account_id="ZER-123")


def test_malformed_trade_row_is_rejected():
    adapter = connected_adapter(FakeKite(trades=[{
        "trade_id": "ZT-1", "order_id": "ZO-1", "quantity": 0,
    }]))
    with pytest.raises(ZerodhaHistoryUnavailable):
        adapter.fetch_today_broker_snapshot(account_id="ZER-123")


def test_order_mutation_methods_remain_disabled():
    adapter = connected_adapter(FakeKite())
    with pytest.raises(NotImplementedError):
        adapter.place_order()
    with pytest.raises(NotImplementedError):
        adapter.cancel_order()
