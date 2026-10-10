from datetime import datetime
from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from threading import Event, Thread
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
                {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "31OCT2099", "token": "1", "symbol": "NIFTY30SEP26FUT", "name": "NIFTY", "lotsize": "75"},
                {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "30NOV2099", "token": "2", "symbol": "NIFTY29OCT26FUT", "name": "NIFTY", "lotsize": "75"},
                {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "31DEC2099", "token": "3", "symbol": "NIFTY26NOV26FUT", "name": "NIFTY", "lotsize": "75"},
                {"exch_seg": "BFO", "instrumenttype": "FUTIDX", "expiry": "30SEP2098", "token": "4", "symbol": "SENSEX30SEP26FUT", "name": "SENSEX", "lotsize": "20"},
                {"exch_seg": "BFO", "instrumenttype": "FUTIDX", "expiry": "30NOV2099", "token": "5", "symbol": "SENSEX30OCT26FUT", "name": "SENSEX", "lotsize": "20"},
                {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2098", "token": "6", "symbol": "SBIN30SEP26FUT", "name": "SBIN", "lotsize": "150"},
                {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "29OCT2099", "token": "7", "symbol": "SBIN29OCT26FUT", "name": "SBIN", "lotsize": "150"},
                {"exch_seg": "MCX", "instrumenttype": "FUTCOM", "expiry": "30SEP2098", "token": "8", "symbol": "CRUDEOIL30SEP26FUT", "name": "CRUDEOIL", "lotsize": "100"},
                {"exch_seg": "MCX", "instrumenttype": "FUTCOM", "expiry": "19DEC2099", "token": "9", "symbol": "CRUDEOIL19OCT26FUT", "name": "CRUDEOIL", "lotsize": "100"},
                {"exch_seg": "MCX", "instrumenttype": "FUTCOM", "expiry": "19NOV2099", "token": "10", "symbol": "CRUDEOIL19NOV26FUT", "name": "CRUDEOIL", "lotsize": "100"},
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
    assert _expiry("30SEP2098").isoformat() == "2098-09-30"
    assert _expiry("2026-09-30").isoformat() == "2026-09-30"
    assert _timestamp_ns({"exchange_timestamp": 1727000000}) == 1727000000 * 1_000_000_000
    assert _timestamp_ns({"exchange_timestamp": 1727000000000}) == 1727000000000 * 1_000_000


def test_calendar_live_timestamp_normalization_accepts_nanoseconds():
    assert _timestamp_ns({"exchange_timestamp": 1727000000000000000}) == 1727000000000000000


def test_calendar_live_payload_source_timestamp_is_second_aligned():
    second = 1727000000123456789 // 1_000_000_000 * 1_000_000_000
    payload = {"source_timestamp_ns": second}
    assert payload["source_timestamp_ns"] == second
    assert payload["source_timestamp_ns"] % 1_000_000_000 == 0


def test_calendar_live_no_contract_retry_is_interruptible(monkeypatch):
    collector = LiveCalendarSpreadOneSecondCollector(
        "unused",
        instrument_master=type("Master", (), {"download": lambda self: []})(),
    )
    entered_wait = Event()
    original_wait = collector.stop_event.wait

    def no_contracts():
        return []

    def tracked_wait(timeout=None):
        entered_wait.set()
        return original_wait(timeout)

    monkeypatch.setattr(collector, "_contracts", no_contracts)
    monkeypatch.setattr(collector.stop_event, "wait", tracked_wait)

    worker = Thread(target=collector._run_session)
    worker.start()
    assert entered_wait.wait(2.0)

    collector.stop()
    worker.join(2.0)

    assert not worker.is_alive()
    assert collector.stop_event.is_set()


def test_calendar_descriptor_preserves_underlying_for_distinct_contract_symbols():
    from datetime import date

    collector = LiveCalendarSpreadOneSecondCollector("unused")
    near = collector._descriptor({"exchange": "NFO", "token": "101", "symbol": "SBIN30OCT26FUT", "kind": "STOCK_FUTURE", "expiry": date(2026, 10, 29), "lot_size": 150, "underlying": "SBIN"})
    far = collector._descriptor({"exchange": "NFO", "token": "102", "symbol": "SBIN27NOV26FUT", "kind": "STOCK_FUTURE", "expiry": date(2026, 11, 26), "lot_size": 150, "underlying": "SBIN"})
    assert near.symbol != far.symbol
    assert near.underlying == far.underlying == "SBIN"


def _calendar_test_record(timestamp_ns, *, bid=None, ask=None, token="test-token"):
    return MarketDataRecord(
        instrument=InstrumentKey(exchange="NFO", segment="NFO", token=token),
        symbol="NIFTY29OCT26FUT",
        instrument_type=InstrumentType.FUTURE,
        timestamp_ns=timestamp_ns,
        ltp=100.0,
        bid=bid,
        ask=ask,
        bid_qty=10 if bid is not None else None,
        ask_qty=10 if ask is not None else None,
        underlying="NIFTY",
        expiry="2026-10-29",
        lot_size=75,
        tick_size=0.05,
    )


def test_calendar_same_second_keeps_valid_quote_and_rejects_older_ticks():
    collector = LiveCalendarSpreadOneSecondCollector("unused")
    emitted = []
    collector.on_observation = emitted.append
    base = int(datetime(2026, 10, 12, 10, 0, tzinfo=IST).timestamp() * 1_000_000_000)

    collector._observe_record(_calendar_test_record(base + 100_000_000))
    collector._observe_record(_calendar_test_record(base + 300_000_000, bid=99.0, ask=100.0))
    # A newer incomplete quote must not erase the executable quote already seen.
    collector._observe_record(_calendar_test_record(base + 700_000_000))
    # A delayed/out-of-order tick must not overwrite the newer quote either.
    collector._observe_record(_calendar_test_record(base + 200_000_000, bid=98.0, ask=99.0))
    collector._observe_record(_calendar_test_record(base + 1_100_000_000, bid=100.0, ask=101.0))

    assert len(emitted) == 1
    assert emitted[0]["bid"] == 99.0
    assert emitted[0]["ask"] == 100.0
    assert emitted[0]["source_timestamp_ns"] == base
    stats = collector.snapshot()["diagnostics"]
    assert stats["same_second_updates"] == 1
    assert stats["same_second_weaker_dropped"] == 1
    assert stats["out_of_order_dropped"] == 1


def test_calendar_persistence_enqueue_failure_does_not_block_live_callback():
    collector = LiveCalendarSpreadOneSecondCollector("unused")
    emitted = []
    collector.on_observation = emitted.append

    class BrokenIngestor:
        def submit_historical(self, _record):
            raise TimeoutError("queue full")

    collector._ingestor = BrokenIngestor()
    record = _calendar_test_record(
        int(datetime(2026, 10, 12, 10, 0, tzinfo=IST).timestamp() * 1_000_000_000),
        bid=99.0,
        ask=100.0,
    )
    collector._emit(record, record.timestamp_ns)

    assert len(emitted) == 1
    assert emitted[0]["bid"] == 99.0
    assert collector.snapshot()["diagnostics"]["persistence_errors"] == 1


def test_calendar_feed_start_failure_still_stops_feed_and_clears_references(monkeypatch):
    import app.market_data.live_calendar_spread_stream as stream

    monkeypatch.setattr(stream.settings, "LIVE_MARKET_DATA_PERSISTENCE_ENABLED", False)

    class BrokenFeed:
        def __init__(self):
            self.stopped = False

        def start(self, _descriptors, _callback):
            raise RuntimeError("subscription startup failed")

        def stop(self):
            self.stopped = True

    feed = BrokenFeed()
    collector = LiveCalendarSpreadOneSecondCollector("unused", feed=feed)
    monkeypatch.setattr(
        collector,
        "_contracts",
        lambda: [{
            "exchange": "NFO", "kind": "INDEX_FUTURE", "token": "1",
            "symbol": "NIFTY29OCT26FUT", "underlying": "NIFTY",
            "expiry": datetime(2026, 10, 29).date(), "lot_size": 75,
        }],
    )
    monkeypatch.setattr(collector, "_descriptor", lambda _contract: object())

    try:
        collector._run_session()
    except RuntimeError as exc:
        assert "subscription startup failed" in str(exc)
    else:
        raise AssertionError("startup error must propagate to the supervisor")

    assert feed.stopped is True
    assert collector._feed is None
    assert collector._ingestor is None
    assert collector._repository is None
