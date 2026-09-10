from __future__ import annotations

from datetime import date, datetime, time, timezone

from app.backtesting.continuous_futures_acquisition import acquire_continuous_futures_history
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalRecord, HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.trading_calendar import TradingCalendar

INTERVAL_NS = 60 * 1_000_000_000


def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


class MultiSessionPartialSource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []
        self.targeted_failures = 0

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        day2_start = _ns(date(2026, 1, 6), time(9, 15))
        if request.start_ns == day2_start:
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 100.0})
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns + 2 * INTERVAL_NS, {"close": 100.0})
            return
        if request.start_ns == _ns(date(2026, 1, 6), time(9, 16)):
            self.targeted_failures += 1
            raise RuntimeError("second session targeted gap failed")
        for timestamp in range(request.start_ns, request.end_ns + 1, INTERVAL_NS):
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, timestamp, {"close": 100.0})


class CompleteSource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        for timestamp in range(request.start_ns, request.end_ns + 1, INTERVAL_NS):
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, timestamp, {"close": 100.0})


def test_multi_session_partial_recovery_resumes_only_incomplete_session(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 18))
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 5), date(2026, 1, 6))

    first_source = MultiSessionPartialSource()
    first = acquire_continuous_futures_history(
        catalog,
        first_source,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="multi-session-job",
        run_id="run-1",
    )

    assert not first.completed
    assert store.get("multi-session-job").state == "progress"
    fingerprint = store.get("multi-session-job").plan_fingerprint
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 7
    assert first_source.requests[0].start_ns == _ns(date(2026, 1, 5), time(9, 15))
    assert first_source.requests[1].start_ns == _ns(date(2026, 1, 6), time(9, 15))
    assert first_source.targeted_failures == 3

    second_source = CompleteSource()
    second = acquire_continuous_futures_history(
        catalog,
        second_source,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="multi-session-job",
        run_id="run-1",
    )

    assert second.completed
    assert store.get("multi-session-job").state == "completed"
    assert store.get("multi-session-job").plan_fingerprint == fingerprint
    assert second_source.requests == [
        HistoricalFetchRequest(
            source="fake",
            instrument="NFO:JAN",
            timeframe="1m",
            start_ns=_ns(date(2026, 1, 6), time(9, 16)),
            end_ns=_ns(date(2026, 1, 6), time(9, 16)),
        )
    ]
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 8
