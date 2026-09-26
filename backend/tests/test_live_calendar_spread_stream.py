from datetime import datetime
from zoneinfo import ZoneInfo

from app.market_data.live_calendar_spread_stream import (
    LiveCalendarSpreadOneSecondCollector,
    _expiry,
    _timestamp_ns,
)

IST = ZoneInfo("Asia/Kolkata")


def test_calendar_live_contracts_select_two_nearest_per_index_stock_and_commodity():
    class Master:
        def download(self):
            return [
                {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "30SEP2026", "token": "1", "symbol": "NIFTY30SEP26FUT", "name": "NIFTY", "lotsize": "75"},
                {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "29OCT2026", "token": "2", "symbol": "NIFTY29OCT26FUT", "name": "NIFTY", "lotsize": "75"},
                {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "26NOV2026", "token": "3", "symbol": "NIFTY26NOV26FUT", "name": "NIFTY", "lotsize": "75"},
                {"exch_seg": "BFO", "instrumenttype": "FUTIDX", "expiry": "30SEP2026", "token": "4", "symbol": "SENSEX30SEP26FUT", "name": "SENSEX", "lotsize": "20"},
                {"exch_seg": "BFO", "instrumenttype": "FUTIDX", "expiry": "30OCT2026", "token": "5", "symbol": "SENSEX30OCT26FUT", "name": "SENSEX", "lotsize": "20"},
                {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026", "token": "6", "symbol": "SBIN30SEP26FUT", "name": "SBIN", "lotsize": "150"},
                {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "29OCT2026", "token": "7", "symbol": "SBIN29OCT26FUT", "name": "SBIN", "lotsize": "150"},
                {"exch_seg": "MCX", "instrumenttype": "FUTCOM", "expiry": "30SEP2026", "token": "8", "symbol": "CRUDEOIL30SEP26FUT", "name": "CRUDEOIL", "lotsize": "100"},
                {"exch_seg": "MCX", "instrumenttype": "FUTCOM", "expiry": "19OCT2026", "token": "9", "symbol": "CRUDEOIL19OCT26FUT", "name": "CRUDEOIL", "lotsize": "100"},
                {"exch_seg": "MCX", "instrumenttype": "FUTCOM", "expiry": "19NOV2026", "token": "10", "symbol": "CRUDEOIL19NOV26FUT", "name": "CRUDEOIL", "lotsize": "100"},
            ]

    collector = LiveCalendarSpreadOneSecondCollector("unused", instrument_master=Master())
    contracts = collector._contracts()
    assert {(x["exchange"], x["underlying"]) for x in contracts} == {
        ("NFO", "NIFTY"), ("BFO", "SENSEX"), ("NFO", "SBIN"), ("MCX", "CRUDEOIL")
    }
    for key in {("NFO", "NIFTY"), ("BFO", "SENSEX"), ("NFO", "SBIN"), ("MCX", "CRUDEOIL")}:
        rows = [x for x in contracts if (x["exchange"], x["underlying"]) == key]
        assert len(rows) == 2
        assert rows[0]["expiry"] < rows[1]["expiry"]


def test_calendar_live_session_hours_are_exchange_specific():
    collector = LiveCalendarSpreadOneSecondCollector("unused")
    assert collector._exchange_open("NFO", datetime(2026, 9, 28, 9, 15, tzinfo=IST))
    assert not collector._exchange_open("NFO", datetime(2026, 9, 28, 15, 31, tzinfo=IST))
    assert collector._exchange_open("MCX", datetime(2026, 9, 28, 9, 0, tzinfo=IST))
    assert collector._exchange_open("MCX", datetime(2026, 9, 28, 23, 30, tzinfo=IST))
    assert not collector._exchange_open("MCX", datetime(2026, 9, 28, 23, 31, tzinfo=IST))
    assert not collector._exchange_open("MCX", datetime(2026, 9, 27, 10, 0, tzinfo=IST))


def test_calendar_live_timestamp_and_expiry_normalization():
    assert _expiry("30SEP2026").isoformat() == "2026-09-30"
    assert _expiry("2026-09-30").isoformat() == "2026-09-30"
    assert _timestamp_ns({"exchange_timestamp": 1727000000}) == 1727000000 * 1_000_000_000
    assert _timestamp_ns({"exchange_timestamp": 1727000000000}) == 1727000000000 * 1_000_000
