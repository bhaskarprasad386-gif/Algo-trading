from __future__ import annotations

from datetime import date, time, datetime, timezone

import pytest

from app.backtesting.continuous_futures_acquisition import (
    acquire_continuous_futures_history,
    build_continuous_futures_acquisition_plan,
    repair_continuous_futures_history_gaps,
)
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.trading_calendar import TradingCalendar

INTERVAL_NS = 60 * 1_000_000_000

class FakeHistoricalSource:
    source_name = "fake"
    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []
    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 100.0})
        if request.end_ns != request.start_ns:
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.end_ns, {"close": 101.0})

class FailOnRequestSource(FakeHistoricalSource):
    def __init__(self, fail_on_call: int) -> None:
        super().__init__()
        self.fail_on_call = fail_on_call
    def fetch(self, request: HistoricalFetchRequest):
        if len(self.requests) + 1 == self.fail_on_call:
            self.requests.append(request)
            raise RuntimeError("temporary provider failure")
        yield from super().fetch(request)

class AlwaysFailHistoricalSource(FakeHistoricalSource):
    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        raise RuntimeError("provider unavailable")
        yield  # pragma: no cover

def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=timezone.utc).timestamp() * 1_000_000_000)

def test_plan_never_crosses_rollover_windows_or_closed_days():
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 16))
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 5), date(2026, 1, 5)),
    )
    plan = build_continuous_futures_acquisition_plan(windows, source="fake", timeframe="1m", interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS)
    assert [(r.instrument, r.start_ns, r.end_ns) for r in plan.requests] == [
        ("NFO:JAN", _ns(date(2026, 1, 2), time(9, 15)), _ns(date(2026, 1, 2), time(9, 16))),
        ("NFO:FEB", _ns(date(2026, 1, 5), time(9, 15)), _ns(date(2026, 1, 5), time(9, 16))),
    ]

def test_acquisition_persists_and_second_run_skips_complete_chunks():
    catalog = HistoricalCatalog(); source = FakeHistoricalSource()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 16))
    windows = (FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)),)
    first = acquire_continuous_futures_history(catalog, source, windows, source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS)
    assert first.completed and first.execution.completed_chunks == 1 and first.execution.skipped_chunks == 0
    assert len(source.requests) == 1 and catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 2
    second = acquire_continuous_futures_history(catalog, source, windows, source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS)
    assert second.completed and second.execution.completed_chunks == 0 and second.execution.skipped_chunks == 1
    assert len(source.requests) == 1 and catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 2

def test_durable_acquisition_resumes_only_unfinished_chunks():
    catalog = HistoricalCatalog(); store = HistoricalJobStore()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 17))
    windows = (FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)),)
    source = FailOnRequestSource(fail_on_call=2)
    first = acquire_continuous_futures_history(catalog, source, windows, source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=INTERVAL_NS, job_store=store, job_id="cash-future-job", run_id="run-1")
    assert not first.completed and first.execution.failed_request_index == 1
    assert store.chunk_state("cash-future-job", 0)[0] == "completed" and store.chunk_state("cash-future-job", 1)[0] == "recoverable"
    resumed_source = FakeHistoricalSource()
    second = acquire_continuous_futures_history(catalog, resumed_source, windows, source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=INTERVAL_NS, job_store=store, job_id="cash-future-job", run_id="run-1")
    assert second.completed and second.execution.failed_request_index is None and len(resumed_source.requests) == 1
    assert store.get("cash-future-job").state == "completed"

def test_durable_arguments_must_be_complete():
    catalog = HistoricalCatalog(); source = FakeHistoricalSource()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 16))
    windows = (FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)),)
    with pytest.raises(ValueError, match="job_store requires both job_id and run_id"):
        acquire_continuous_futures_history(catalog, source, windows, source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=INTERVAL_NS, job_store=HistoricalJobStore(), job_id="job-only")

def test_durable_gap_repair_persists_and_resumes(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite"); store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 18))
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)); session = calendar.sessions_between(window.start_date, window.end_date)[0]
    catalog.ingest(HistoricalRecord("fake", "NFO:JAN", "1m", timestamp, {"close": 100.0}) for timestamp in (session.start_ns, session.start_ns + 2 * INTERVAL_NS, session.end_ns))
    source = FakeHistoricalSource()
    first = repair_continuous_futures_history_gaps(catalog, source, [window], source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store, job_id="gap-repair-job", run_id="run-1")
    assert first.completed and len(source.requests) == 1
    assert source.requests[0].start_ns == session.start_ns + INTERVAL_NS and source.requests[0].end_ns == session.start_ns + INTERVAL_NS
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 4
    second = repair_continuous_futures_history_gaps(catalog, source, [window], source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store, job_id="gap-repair-job", run_id="run-1")
    assert second.completed and second.execution.completed_chunks == 0 and second.execution.skipped_chunks == 0 and len(source.requests) == 1

def test_durable_gap_repair_recovers_after_provider_failure(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite"); store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 18))
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)); session = calendar.sessions_between(window.start_date, window.end_date)[0]
    catalog.ingest(HistoricalRecord("fake", "NFO:JAN", "1m", timestamp, {"close": 100.0}) for timestamp in (session.start_ns, session.start_ns + 2 * INTERVAL_NS, session.end_ns))
    failing_source = AlwaysFailHistoricalSource()
    first = repair_continuous_futures_history_gaps(catalog, failing_source, [window], source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store, job_id="gap-repair-recovery", run_id="run-1")
    assert not first.completed and first.execution.failed_request_index == 0
    assert store.chunk_state("gap-repair-recovery", 0)[0] == "recoverable"
    resumed_source = FakeHistoricalSource()
    second = repair_continuous_futures_history_gaps(catalog, resumed_source, [window], source_name="fake", timeframe="1m", interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS, job_store=store, job_id="gap-repair-recovery", run_id="run-1")
    assert second.completed and second.execution.failed_request_index is None and len(resumed_source.requests) == 1
    assert resumed_source.requests[0].start_ns == session.start_ns + INTERVAL_NS and store.get("gap-repair-recovery").state == "completed"

def test_gap_repair_handles_multiple_contracts_and_multiple_internal_gaps(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite"); store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 19))
    jan = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2))
    feb = FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 5), date(2026, 1, 5))
    sessions = {
        "NFO:JAN": calendar.sessions_between(jan.start_date, jan.end_date)[0],
        "NFO:FEB": calendar.sessions_between(feb.start_date, feb.end_date)[0],
    }
    catalog.ingest(
        HistoricalRecord("fake", instrument, "1m", timestamp, {"close": 100.0})
        for instrument, session in sessions.items()
        for timestamp in (
            session.start_ns,
            session.start_ns + 2 * INTERVAL_NS,
            session.start_ns + 4 * INTERVAL_NS,
            session.end_ns,
        )
    )
    failing_source = FailOnRequestSource(fail_on_call=2)
    first = repair_continuous_futures_history_gaps(
        catalog, failing_source, [jan, feb], source_name="fake", timeframe="1m",
        interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS,
        job_store=store, job_id="multi-gap-repair", run_id="run-1",
    )
    assert not first.completed and first.execution.failed_request_index == 1
    assert len(first.plan.requests) == 4
    assert store.chunk_state("multi-gap-repair", 0)[0] == "completed"
    assert store.chunk_state("multi-gap-repair", 1)[0] == "recoverable"
    fingerprint = store.get("multi-gap-repair").plan_fingerprint

    resumed_source = FakeHistoricalSource()
    second = repair_continuous_futures_history_gaps(
        catalog, resumed_source, [jan, feb], source_name="fake", timeframe="1m",
        interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS,
        job_store=store, job_id="multi-gap-repair", run_id="run-1",
    )
    assert second.completed and second.execution.failed_request_index is None
    assert len(resumed_source.requests) == 3
    assert [request.instrument for request in resumed_source.requests] == ["NFO:JAN", "NFO:FEB", "NFO:FEB"]
    assert store.get("multi-gap-repair").plan_fingerprint == fingerprint
    assert store.get("multi-gap-repair").state == "completed"
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 6
    assert catalog.count(source="fake", instrument="NFO:FEB", timeframe="1m") == 6
