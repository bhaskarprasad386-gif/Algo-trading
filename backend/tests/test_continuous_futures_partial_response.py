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


def test_partial_provider_response_is_recoverable_and_resume_completes(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 18))
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2))
    source = PartialThenCompleteSource()

    first = acquire_continuous_futures_history(
        catalog,
        source,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        executor=None,
        job_store=store,
        job_id="partial-response-job",
        run_id="run-1",
    )

    assert not first.completed
    assert first.execution.failed_request_index == 0
    assert store.chunk_state("partial-response-job", 0)[0] == "recoverable"
    fingerprint = store.get("partial-response-job").plan_fingerprint
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 1
    assert source.calls == 3  # one initial partial response plus two retries

    resumed_source = PartialThenCompleteSource()
    second = acquire_continuous_futures_history(
        catalog,
        resumed_source,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="partial-response-job",
        run_id="run-1",
    )

    assert second.completed
    assert second.execution.failed_request_index is None
    assert store.get("partial-response-job").state == "completed"
    assert store.get("partial-response-job").plan_fingerprint == fingerprint
    session = calendar.sessions_between(window.start_date, window.end_date)[0]
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 4
    assert _ns(date(2026, 1, 2), time(9, 17)) == session.end_ns - INTERVAL_NS
