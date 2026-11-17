import pytest

from app.brokers.shoonya import ShoonyaAdapter, ShoonyaHistoryUnavailable


_DEFAULT = object()


class FakeShoonya:
    def __init__(self, profile=_DEFAULT, orders=_DEFAULT, trades=_DEFAULT,
                 profile_error=None, order_error=None, trade_error=None):
        self.profile_data = {"stat": "Ok", "uid": "SHOONYA-UID", "actid": "SH-123"} if profile is _DEFAULT else profile
        self.order_rows = [] if orders is _DEFAULT else orders
        self.trade_rows = [] if trades is _DEFAULT else trades
        self.profile_error = profile_error
        self.order_error = order_error
        self.trade_error = trade_error
        self.calls = []

    def get_user_details(self):
        self.calls.append("profile")
        if self.profile_error:
            raise self.profile_error
        return self.profile_data

    def get_order_book(self):
        self.calls.append("orders")
        if self.order_error:
            raise self.order_error
        return self.order_rows

    def get_trade_book(self):
        self.calls.append("trades")
        if self.trade_error:
            raise self.trade_error
        return self.trade_rows


def connected_adapter(client):
    adapter = ShoonyaAdapter(client)
    result = adapter.connect(uid="SHOONYA-UID", account_id="SH-123")
    assert result == {
        "broker": "shoonya", "connected": True,
        "uid": "SHOONYA-UID", "account_id": "SH-123",
    }
    return adapter


def test_normalizes_complete_order_and_trade_books_read_only():
    client = FakeShoonya(
        orders=[{
            "stat": "Ok", "norenordno": "SO-1", "tsym": "NIFTY26NOV25000CE",
            "trantype": "B", "qty": "50", "fillshares": "25",
        }],
        trades=[{
            "stat": "Ok", "flid": "SF-1", "norenordno": "SO-1", "flqty": "25", "flprc": "101.25", "fltm": "01-01-1980 00:00:00", "exch_tm": "10:31:00 16-11-2026",
        }],
    )
    result = connected_adapter(client).fetch_today_broker_snapshot(account_id="SH-123")
    assert result.scope == "CURRENT_TRADING_DAY"
    assert result.trading_date
    assert result.snapshot.complete is True
    assert result.snapshot.account_id == "SH-123"
    assert result.snapshot.broker == "shoonya"
    assert result.snapshot.orders[0].broker_order_id == "SO-1"
    assert result.snapshot.orders[0].side == "BUY"
    assert result.snapshot.orders[0].filled_quantity == 25
    assert result.snapshot.trades[0].broker_trade_id == "SF-1"
    assert result.snapshot.trades[0].quantity == 25
    assert result.snapshot.trades[0].fill_price == 101.25
    assert result.snapshot.trades[0].occurred_at_ns is not None
    assert client.calls == ["profile", "orders", "trades"]


def test_successful_empty_books_are_complete():
    result = connected_adapter(FakeShoonya()).fetch_today_broker_snapshot(account_id="SH-123")
    assert result.snapshot.complete is True
    assert result.snapshot.orders == ()
    assert result.snapshot.trades == ()


@pytest.mark.parametrize("profile", [
    None, [], {"stat": "Not_Ok", "emsg": "expired"},
    {"stat": "Ok", "uid": "", "actid": "SH-123"},
])
def test_invalid_profile_fails_closed(profile):
    client = FakeShoonya(profile=profile)
    adapter = ShoonyaAdapter(client)
    with pytest.raises(ShoonyaHistoryUnavailable):
        adapter.connect(uid="SHOONYA-UID", account_id="SH-123")
    assert adapter.connected is False


@pytest.mark.parametrize("profile", [
    {"stat": "Ok", "uid": "OTHER", "actid": "SH-123"},
    {"stat": "Ok", "uid": "SHOONYA-UID", "actid": "OTHER"},
])
def test_uid_or_account_mismatch_fails_closed(profile):
    adapter = ShoonyaAdapter(FakeShoonya(profile=profile))
    with pytest.raises(ShoonyaHistoryUnavailable, match="does not match"):
        adapter.connect(uid="SHOONYA-UID", account_id="SH-123")
    assert adapter.connected is False


@pytest.mark.parametrize("rows", [None, {}, ["bad-row"]])
def test_malformed_order_book_never_becomes_complete(rows):
    adapter = connected_adapter(FakeShoonya(orders=rows))
    with pytest.raises(ShoonyaHistoryUnavailable):
        adapter.fetch_today_broker_snapshot(account_id="SH-123")


def test_unsuccessful_book_row_fails_closed():
    adapter = connected_adapter(FakeShoonya(orders=[{
        "stat": "Not_Ok", "emsg": "session expired",
    }]))
    with pytest.raises(ShoonyaHistoryUnavailable):
        adapter.fetch_today_broker_snapshot(account_id="SH-123")


def test_trade_book_failure_never_returns_partial_snapshot():
    adapter = connected_adapter(FakeShoonya(trade_error=RuntimeError("temporary")))
    with pytest.raises(ShoonyaHistoryUnavailable, match="request failed"):
        adapter.fetch_today_broker_snapshot(account_id="SH-123")


def test_wrong_account_and_disconnected_session_are_rejected():
    adapter = connected_adapter(FakeShoonya())
    with pytest.raises(ShoonyaHistoryUnavailable, match="account scope"):
        adapter.fetch_today_broker_snapshot(account_id="OTHER")
    adapter.disconnect()
    with pytest.raises(ShoonyaHistoryUnavailable, match="not connected"):
        adapter.fetch_today_broker_snapshot(account_id="SH-123")


@pytest.mark.parametrize("row", [
    {"stat": "Ok", "norenordno": "SO-1", "tsym": "ABC", "trantype": "X", "qty": "10", "fillshares": "0"},
    {"stat": "Ok", "norenordno": "SO-1", "tsym": "ABC", "trantype": "B", "qty": "0", "fillshares": "0"},
    {"stat": "Ok", "norenordno": "SO-1", "tsym": "ABC", "trantype": "B", "qty": "10", "fillshares": "11"},
])
def test_malformed_order_rows_are_rejected(row):
    adapter = connected_adapter(FakeShoonya(orders=[row]))
    with pytest.raises((ShoonyaHistoryUnavailable, ValueError)):
        adapter.fetch_today_broker_snapshot(account_id="SH-123")


def test_malformed_trade_row_is_rejected():
    adapter = connected_adapter(FakeShoonya(trades=[{
        "stat": "Ok", "flid": "SF-1", "norenordno": "SO-1", "flqty": "0",
    }]))
    with pytest.raises(ShoonyaHistoryUnavailable):
        adapter.fetch_today_broker_snapshot(account_id="SH-123")


def test_live_order_mutations_remain_disabled():
    adapter = connected_adapter(FakeShoonya())
    with pytest.raises(NotImplementedError):
        adapter.place_order()
    with pytest.raises(NotImplementedError):
        adapter.cancel_order()
