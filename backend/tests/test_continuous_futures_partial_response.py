from __future__ import annotations

from datetime import date, time, datetime, timezone

from app.backtesting.continuous_futures_acquisition import acquire_continuous_futures_history
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.trading_calendar import TradingCalendar

INTERVAL_NS = 60 * 1_000_000_000


def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


class PartialThenCompleteSource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []
        self.calls = 0

    def fetch(self, request: HistoricalFetchRequest):
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 100.0})
            return
        for timestamp in range(request.start_ns, request.end_ns + 1, INTERVAL_NS):
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, timestamp, {"close": 100.0})


def test_partial_provider_response_retries_only_missing_range(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 18))
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2))
    source = PartialThenCompleteSource()

    report = acquire_continuous_futures_history(
        catalog, source, [window], source_name="fake", timeframe="1m",
        interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS,
        job_store=store, job_id="partial-response-job", run_id="run-1",
    )

    assert report.completed
    assert report.execution.failed_request_index is None
    assert store.get("partial-response-job").state == "completed"
    assert source.calls == 2
    assert source.requests[0].start_ns < source.requests[1].start_ns
    assert source.requests[1].start_ns == _ns(date(2026, 1, 2), time(9, 16))
    assert source.requests[1].end_ns == source.requests[0].end_ns
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 4


def test_partial_response_durable_resume_keeps_plan_fingerprint(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 18))
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2))

    class AlwaysPartial:
        source_name = "fake"
        def fetch(self, request: HistoricalFetchRequest):
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 100.0})

    first = acquire_continuous_futures_history(
        catalog, AlwaysPartial(), [window], source_name="fake", timeframe="1m",
        interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS,
        job_store=store, job_id="resume-job", run_id="run-1",
    )
    assert not first.completed
    fingerprint = store.get("resume-job").plan_fingerprint

    complete = PartialThenCompleteSource()
    second = acquire_continuous_futures_history(
        catalog, complete, [window], source_name="fake", timeframe="1m",
        interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS,
        job_store=store, job_id="resume-job", run_id="run-1",
    )
    assert second.completed
    assert store.get("resume-job").plan_fingerprint == fingerprint
    assert store.get("resume-job").state == "completed"
