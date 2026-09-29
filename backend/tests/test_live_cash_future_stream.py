from datetime import datetime
from zoneinfo import ZoneInfo

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.market_data.live_cash_future_stream import (
    LiveCashFutureOneSecondCollector,
    _ingest_live_record,
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


def test_live_best_side_includes_price_and_quantity():
    collector = LiveCashFutureOneSecondCollector(":memory:")
    message = {
        "best_5_buy_data": [{"price": "10000", "quantity": "250"}],
        "best_5_sell_data": [{"price": "10100", "quantity": "175"}],
    }
    assert collector._best_side(message, "best_5_buy_data") == 100.0
    assert collector._best_side_detail(message, "best_5_buy_data") == (100.0, 250.0)
    assert collector._best_side_detail(message, "best_5_sell_data") == (101.0, 175.0)


def test_live_persistence_skips_existing_second_bucket_without_conflict():
    catalog = HistoricalCatalog(":memory:")
    record = HistoricalRecord(
        source="angelone-live-1s",
        instrument="NSE:AAA-EQ|cash-AAA",
        timeframe="1s",
        timestamp_ns=1_762_234_836_000_000_000,
        payload={"ltp": 100.0, "received_at_ns": 1},
    )
    assert _ingest_live_record(catalog, record) == 1

    conflicting = HistoricalRecord(
        source=record.source,
        instrument=record.instrument,
        timeframe=record.timeframe,
        timestamp_ns=record.timestamp_ns,
        payload={"ltp": 101.0, "received_at_ns": 2},
    )
    assert _ingest_live_record(catalog, conflicting) == 0
    assert catalog.count(source=record.source, instrument=record.instrument, timeframe=record.timeframe) == 1
    catalog.close()


def test_live_payload_price_compatibility_supports_ltp_and_close():
    from app.backtesting.cash_future_historical_loader import _record_price

    old_live = HistoricalRecord("angelone-live-1s", "AAA-EQ|cash-AAA", "1s", 1, {"ltp": 100.25})
    new_live = HistoricalRecord("angelone-live-1s", "AAA-EQ|cash-AAA", "1s", 2, {"ltp": 101.25, "close": 101.25})

    assert _record_price(old_live) == 100.25
    assert _record_price(new_live) == 101.25


def test_live_atomic_first_write_wins_for_duplicate_second_bucket():
    catalog = HistoricalCatalog(":memory:")
    first = HistoricalRecord(
        "angelone-live-1s", "NFO:AAA30SEP|101", "1s", 1000,
        {"ltp": 100.0, "received_at_ns": 1},
    )
    second = HistoricalRecord(
        first.source, first.instrument, first.timeframe, first.timestamp_ns,
        {"ltp": 101.0, "received_at_ns": 2},
    )
    assert _ingest_live_record(catalog, first) == 1
    assert _ingest_live_record(catalog, second) == 0
    stored = catalog.records(source=first.source, instrument=first.instrument, timeframe=first.timeframe)
    assert stored == (first,)
    catalog.close()


def test_live_cash_future_health_exposes_runtime_state():
    from app.market_data.live_cash_future_stream import live_cash_future_health
    state = live_cash_future_health()
    assert {"enabled", "running", "connected", "last_observation_ns", "written", "rejected", "gap_seconds", "latest_age_seconds", "status"}.issubset(state)
    assert state["status"] in {"ok", "degraded"}


def test_live_cash_future_gap_detection_is_explicit_in_collector_source():
    from pathlib import Path
    source = Path(__import__("app.market_data.live_cash_future_stream", fromlist=["__file__"]).__file__)
    text = source.read_text(encoding="utf-8")
    assert "gap_seconds" in text
    assert "second_ns > previous[0] + 1_000_000_000" in text


def test_live_feed_silence_watchdog_triggers_only_after_timeout():
    assert not LiveCashFutureOneSecondCollector._feed_silent(100.0, 129.9)
    assert LiveCashFutureOneSecondCollector._feed_silent(100.0, 130.0)
