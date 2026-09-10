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


class PartialThenFailSource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 100.0})
        raise RuntimeError("targeted recovery failed")


class CompleteSource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        for timestamp in range(request.start_ns, request.end_ns + 1, INTERVAL_NS):
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, timestamp, {"close": 100.0})


class InitialDisjointSource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        for timestamp in (request.start_ns, request.start_ns + 2 * INTERVAL_NS):
            if timestamp <= request.end_ns:
                yield HistoricalRecord(request.source, request.instrument, request.timeframe, timestamp, {"close": 100.0})


def test_disjoint_targeted_recovery_failure_then_resume_repairs_only_remaining_gap(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 18))
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2))

    first = acquire_continuous_futures_history(
        catalog,
        InitialDisjointSource(),
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="disjoint-failure-job",
        run_id="run-1",
    )
    assert not first.completed
    assert store.get("disjoint-failure-job").state == "progress"
    fingerprint = store.get("disjoint-failure-job").plan_fingerprint

    failing = PartialThenFailSource()
    second = acquire_continuous_futures_history(
        catalog,
        failing,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="disjoint-failure-job",
        run_id="run-1",
    )
    assert not second.completed
    assert store.get("disjoint-failure-job").state == "progress"
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 3
    assert all(request.start_ns == request.end_ns for request in failing.requests)
    assert failing.requests[0].start_ns == _ns(date(2026, 1, 2), time(9, 16))
    assert failing.requests[-1].start_ns == _ns(date(2026, 1, 2), time(9, 18))
    assert store.get("disjoint-failure-job").plan_fingerprint == fingerprint

    complete = CompleteSource()
    third = acquire_continuous_futures_history(
        catalog,
        complete,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="disjoint-failure-job",
        run_id="run-1",
    )
    assert third.completed
    assert store.get("disjoint-failure-job").state == "completed"
    assert store.get("disjoint-failure-job").plan_fingerprint == fingerprint
    assert complete.requests == [
        HistoricalFetchRequest(
            source="fake",
            instrument="NFO:JAN",
            timeframe="1m",
            start_ns=_ns(date(2026, 1, 2), time(9, 18)),
            end_ns=_ns(date(2026, 1, 2), time(9, 18)),
        )
    ]
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 4
