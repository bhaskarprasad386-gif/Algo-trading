from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import pytest

from app.brokers.fyers import FyersAdapter
from app.brokers.fyers_history import FyersHistoryProvider
from app.brokers.history import BrokerHistoryUnavailable


IST = ZoneInfo("Asia/Kolkata")


class FakeFyersReports:
    def __init__(self, order_pages=None, trade_pages=None):
        self.profile = {"s": "ok", "code": 200, "data": {"fy_id": "FY-123"}}
        self.order_pages = order_pages or {1: [{"id_fyers": "FO-1", "symbol": "NSE:NIFTY26NOV25000CE",
                                                "transaction_type": "BUY", "qty": 50, "tradedQty": 25}]}
        self.trade_pages = trade_pages or {1: [{"tradeNumber": "FT-1", "orderNumber": "FO-1",
                                                 "side": 1, "traded_qty": 25,
                                                 "trade_price": 101.25,
                                                 "orderDateTime": "16-Nov-2026 10:31:00"}]}
        self.calls = []

    def get_profile(self):
        return self.profile

    def orderhistory(self, *, data):
        self.calls.append(("orders", dict(data)))
        rows = self.order_pages.get(int(data["page_no"]), [])
        return {"s": "ok", "code": 200, "data": rows}

    def tradehistory(self, *, data):
        self.calls.append(("trades", dict(data)))
        rows = self.trade_pages.get(int(data["page_no"]), [])
        return {"s": "ok", "data": rows}


def _connected(client):
    adapter = FyersAdapter(client)
    adapter.connect(account_id="FY-123")
    return FyersHistoryProvider(adapter)


def _closed_window(days=2):
    today = datetime.now(IST).date()
    end_date = today
    start_date = end_date - timedelta(days=days)
    start = datetime.combine(start_date, time.min, tzinfo=IST)
    end = datetime.combine(end_date, time.min, tzinfo=IST)
    return start, end


def test_fyers_provider_fetches_date_range_and_normalizes_both_reports():
    client = FakeFyersReports()
    provider = _connected(client)
    start, end = _closed_window()
    result = provider.fetch_order_trade_snapshot(
        account_id="FY-123", window_start=start, window_end=end,
    )
    assert result.complete is True
    assert result.provider_window == "FYERS_CLOSED_DAY_DATE_RANGE_REPORTS"
    assert result.snapshot.account_id == "FY-123"
    assert result.snapshot.orders[0].broker_order_id == "FO-1"
    assert result.snapshot.trades[0].broker_trade_id == "FT-1"
    assert result.snapshot.trades[0].fill_price == 101.25
    assert result.snapshot.trades[0].occurred_at_ns is not None
    assert result.pages_fetched == 2
    for _, request in client.calls:
        assert request["from_date"] == start.date().isoformat()
        assert request["to_date"] == (end.date() - timedelta(days=1)).isoformat()
        assert request["page_size"] == "100"
        assert request["page_no"] == "1"


def test_fyers_provider_reads_until_short_page_and_counts_pages():
    client = FakeFyersReports(order_pages={
        1: [{"id_fyers": f"FO-{i}", "symbol": "NSE:ABC", "transaction_type": "BUY",
             "qty": 10, "tradedQty": 0} for i in range(100)],
        2: [{"id_fyers": "FO-LAST", "symbol": "NSE:ABC", "transaction_type": "SELL",
             "qty": 10, "tradedQty": 0}],
    })
    provider = _connected(client)
    start, end = _closed_window()
    result = provider.fetch_order_trade_snapshot(
        account_id="FY-123", window_start=start, window_end=end,
    )
    assert len(result.snapshot.orders) == 101
    assert result.pages_fetched == 3
    assert [call[1]["page_no"] for call in client.calls if call[0] == "orders"] == ["1", "2"]


def test_fyers_provider_rejects_current_day_window_before_report_calls():
    client = FakeFyersReports()
    provider = _connected(client)
    start = datetime.combine(datetime.now(IST).date(), time.min, tzinfo=IST)
    end = start + timedelta(days=1)
    with pytest.raises(BrokerHistoryUnavailable, match="closed IST days"):
        provider.fetch_order_trade_snapshot(
            account_id="FY-123", window_start=start, window_end=end,
        )
    assert client.calls == []


def test_fyers_provider_rejects_partial_day_window():
    client = FakeFyersReports()
    provider = _connected(client)
    start, end = _closed_window()
    with pytest.raises(BrokerHistoryUnavailable, match="complete IST calendar-day"):
        provider.fetch_order_trade_snapshot(
            account_id="FY-123", window_start=start + timedelta(hours=1), window_end=end,
        )
    assert client.calls == []


def test_fyers_provider_rejects_wrong_account_before_report_calls():
    client = FakeFyersReports()
    provider = _connected(client)
    start, end = _closed_window()
    with pytest.raises(BrokerHistoryUnavailable, match="account scope"):
        provider.fetch_order_trade_snapshot(
            account_id="OTHER", window_start=start, window_end=end,
        )
    assert client.calls == []


def test_fyers_provider_rejects_failed_report_without_partial_snapshot():
    client = FakeFyersReports()
    client.tradehistory = lambda *, data: {"s": "error", "code": -8, "data": []}
    provider = _connected(client)
    start, end = _closed_window()
    with pytest.raises(BrokerHistoryUnavailable, match="trade-history"):
        provider.fetch_order_trade_snapshot(
            account_id="FY-123", window_start=start, window_end=end,
        )


def test_fyers_provider_rejects_duplicate_order_ids():
    duplicate = {"id_fyers": "FO-1", "symbol": "NSE:ABC", "transaction_type": "BUY",
                 "qty": 10, "tradedQty": 0}
    client = FakeFyersReports(order_pages={1: [duplicate, duplicate]})
    provider = _connected(client)
    start, end = _closed_window()
    with pytest.raises(BrokerHistoryUnavailable, match="duplicate order IDs"):
        provider.fetch_order_trade_snapshot(
            account_id="FY-123", window_start=start, window_end=end,
        )
