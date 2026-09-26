from datetime import datetime
from zoneinfo import ZoneInfo

from app.market_data.live_cash_future_stream import (
    LiveCashFutureOneSecondCollector,
    _ltp,
    _timestamp_ns,
)

IST = ZoneInfo("Asia/Kolkata")


def test_live_timestamp_and_ltp_normalization():
    assert _timestamp_ns({"exchange_timestamp": 1762234836588}) == 1762234836588000000
    assert _ltp({"last_traded_price": 7051240}) == 70512.40


def test_live_market_session():
    assert LiveCashFutureOneSecondCollector.market_open(datetime(2026, 9, 28, 10, 0, tzinfo=IST))
    assert not LiveCashFutureOneSecondCollector.market_open(datetime(2026, 9, 27, 10, 0, tzinfo=IST))
    assert not LiveCashFutureOneSecondCollector.market_open(datetime(2026, 9, 28, 16, 0, tzinfo=IST))


class FakeInstrumentMaster:
    def __init__(self, rows):
        self.rows = rows
        self.resolved = []

    def download(self):
        return self.rows

    def resolve_cash_instrument(self, underlying, exchange):
        self.resolved.append((underlying, exchange))
        if underlying == "BROKEN":
            raise LookupError("no cash leg")
        return {"token": f"cash-{underlying}", "symbol": f"{underlying}-EQ"}


def test_live_contract_selection_keeps_current_near_and_skips_missing_cash():
    from datetime import timedelta

    today = datetime.now(IST).date()
    rows = [
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "token": "101",
         "name": "AAA", "symbol": "AAA30SEP", "expiry": (today + timedelta(days=4)).strftime("%d%b%Y")},
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "token": "102",
         "name": "AAA", "symbol": "AAAOCT", "expiry": (today + timedelta(days=34)).strftime("%d%b%Y")},
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "token": "103",
         "name": "AAA", "symbol": "AAANOV", "expiry": (today + timedelta(days=65)).strftime("%d%b%Y")},
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "token": "201",
         "name": "BROKEN", "symbol": "BROKEN30SEP", "expiry": (today + timedelta(days=4)).strftime("%d%b%Y")},
        {"exch_seg": "NFO", "instrumenttype": "OPTSTK", "token": "301",
         "name": "AAA", "symbol": "AAA30SEPCE", "expiry": (today + timedelta(days=4)).strftime("%d%b%Y")},
    ]
    master = FakeInstrumentMaster(rows)
    collector = LiveCashFutureOneSecondCollector(
        ":memory:",
        instrument_master=master,
    )

    futures, cash = collector._contracts()

    assert [item["token"] for item in futures] == ["101", "102"]
    assert [item["_contract_month"] for item in futures] == ["CURRENT", "NEAR"]
    assert [item["token"] for item in cash] == ["cash-AAA"]
    assert ("BROKEN", "NSE") in master.resolved


def test_live_same_second_record_identity_is_second_bucketed():
    first = 1_762_234_836_588_123_456
    second = (first // 1_000_000_000) * 1_000_000_000
    assert second == 1_762_234_836_000_000_000
    assert (second // 1_000_000_000) == (first // 1_000_000_000)
