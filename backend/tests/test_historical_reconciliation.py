from __future__ import annotations

from datetime import date, time, datetime, timezone

from app.backtesting.continuous_futures_acquisition import acquire_continuous_futures_history
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.trading_calendar import TradingCalendar


INTERVAL_NS = 60 * 1_000_000_000


class Source:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 100.0})
        if request.end_ns != request.start_ns:
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.end_ns, {"close": 101.0})


def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_durable_resume_reopens_completed_chunk_when_catalog_data_is_missing():
    catalog = HistoricalCatalog()
    store = HistoricalJobStore()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 16))
    windows = (FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)),)
    source = Source()

    first = acquire_continuous_futures_history(
        catalog, source, windows, source_name="fake", timeframe="1m",
        interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS,
        job_store=store, job_id="reconcile-job", run_id="run-1",
    )
    assert first.completed
    assert store.chunk_state("reconcile-job", 0)[0] == "completed"
    assert len(source.requests) == 1

    catalog._db.execute(
        "DELETE FROM data_catalog WHERE source=? AND instrument=? AND timeframe=? AND timestamp_ns=?",
        ("fake", "NFO:JAN", "1m", _ns(date(2026, 1, 2), time(9, 16))),
    )
    catalog._db.commit()

    resumed_source = Source()
    second = acquire_continuous_futures_history(
        catalog, resumed_source, windows, source_name="fake", timeframe="1m",
        interval_ns=INTERVAL_NS, calendar=calendar, max_request_ns=10 * INTERVAL_NS,
        job_store=store, job_id="reconcile-job", run_id="run-1",
    )
    assert second.completed
    assert second.execution.completed_chunks == 1
    assert second.execution.skipped_chunks == 0
    assert len(resumed_source.requests) == 1
    assert store.chunk_state("reconcile-job", 0)[0] == "completed"
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 2
