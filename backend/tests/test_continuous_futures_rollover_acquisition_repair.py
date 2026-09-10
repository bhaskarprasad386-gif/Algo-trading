from datetime import date, datetime, time, timezone

from app.backtesting.continuous_futures_acquisition import acquire_continuous_futures_history
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.trading_calendar import TradingCalendar


INTERVAL_NS = 2 * 60 * 60 * 1_000_000_000
SOURCE_NAME = "test-provider"
TIMEFRAME = "2h"


def _ns(day: date, clock: time) -> int:
    return int(datetime.combine(day, clock, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


class _RecordingSource:
    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        timestamps = range(request.start_ns, request.end_ns + 1, INTERVAL_NS)
        for timestamp_ns in timestamps:
            yield HistoricalRecord(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
                timestamp_ns=timestamp_ns,
                payload={"instrument": request.instrument, "timestamp_ns": timestamp_ns},
            )


def _windows() -> tuple[FNORolloverWindow, FNORolloverWindow]:
    return (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "101", date(2026, 1, 29), date(2026, 1, 29)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "202", date(2026, 1, 30), date(2026, 1, 30)),
    )


def _session_timestamps(day: date) -> tuple[int, ...]:
    return tuple(_ns(day, clock) for clock in (time(9, 15), time(11, 15), time(13, 15), time(15, 15)))


def test_rollover_acquisition_repairs_each_contract_gap_with_its_own_token() -> None:
    catalog = HistoricalCatalog()
    old_times = _session_timestamps(date(2026, 1, 29))
    new_times = _session_timestamps(date(2026, 1, 30))

    catalog.ingest(
        [
            HistoricalRecord(SOURCE_NAME, "NFO:101", TIMEFRAME, timestamp, {"contract": "101"})
            for timestamp in old_times[:-1]
        ]
        + [
            HistoricalRecord(SOURCE_NAME, "NFO:202", TIMEFRAME, timestamp, {"contract": "202"})
            for timestamp in new_times[1:]
        ]
    )

    source = _RecordingSource()
    report = acquire_continuous_futures_history(
        catalog,
        source,
        _windows(),
        source_name=SOURCE_NAME,
        timeframe=TIMEFRAME,
        interval_ns=INTERVAL_NS,
        calendar=TradingCalendar(),
        max_request_ns=INTERVAL_NS,
    )

    assert report.completed
    assert [(request.instrument, request.start_ns, request.end_ns) for request in source.requests] == [
        ("NFO:101", old_times[-1], old_times[-1]),
        ("NFO:202", new_times[0], new_times[0]),
    ]
    assert [record.timestamp_ns for record in catalog.records(source=SOURCE_NAME, instrument="NFO:101", timeframe=TIMEFRAME)] == list(old_times)
    assert [record.timestamp_ns for record in catalog.records(source=SOURCE_NAME, instrument="NFO:202", timeframe=TIMEFRAME)] == list(new_times)
    assert all(record.payload["contract"] == "101" for record in catalog.records(source=SOURCE_NAME, instrument="NFO:101", timeframe=TIMEFRAME)[:-1])
    assert catalog.records(source=SOURCE_NAME, instrument="NFO:101", timeframe=TIMEFRAME)[-1].payload["instrument"] == "NFO:101"
    assert catalog.records(source=SOURCE_NAME, instrument="NFO:202", timeframe=TIMEFRAME)[0].payload["instrument"] == "NFO:202"


def test_rollover_acquisition_does_not_create_weekend_repair_requests() -> None:
    catalog = HistoricalCatalog()
    friday = date(2026, 1, 30)
    monday = date(2026, 2, 2)
    friday_times = _session_timestamps(friday)
    monday_times = _session_timestamps(monday)
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "101", friday, friday),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "202", monday, monday),
    )
    catalog.ingest(
        [HistoricalRecord(SOURCE_NAME, "NFO:101", TIMEFRAME, timestamp, {}) for timestamp in friday_times[:-1]]
        + [HistoricalRecord(SOURCE_NAME, "NFO:202", TIMEFRAME, timestamp, {}) for timestamp in monday_times[:-1]]
    )

    source = _RecordingSource()
    report = acquire_continuous_futures_history(
        catalog,
        source,
        windows,
        source_name=SOURCE_NAME,
        timeframe=TIMEFRAME,
        interval_ns=INTERVAL_NS,
        calendar=TradingCalendar(),
        max_request_ns=INTERVAL_NS,
    )

    assert report.completed
    assert [request.instrument for request in source.requests] == ["NFO:101", "NFO:202"]
    assert all(request.start_ns != request.end_ns or request.start_ns in {friday_times[-1], monday_times[-1]} for request in source.requests)
    assert catalog.count(source=SOURCE_NAME, timeframe=TIMEFRAME) == 8
