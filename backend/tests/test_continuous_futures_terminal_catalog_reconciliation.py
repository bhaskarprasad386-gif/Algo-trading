from datetime import date

from app.backtesting.continuous_futures_acquisition import (
    acquire_continuous_futures_history,
    build_continuous_futures_acquisition_plan,
)
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.trading_calendar import TradingCalendar


class RecordingSource:
    def __init__(self, records_by_request):
        self.records_by_request = records_by_request
        self.calls = []

    def fetch(self, request):
        self.calls.append(request)
        yield from self.records_by_request[request]


def _records(plan, *, interval_ns):
    return {
        request: tuple(
            HistoricalRecord(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
                timestamp_ns=timestamp,
                payload={"close": 100.0},
            )
            for timestamp in range(request.start_ns, request.end_ns + 1, interval_ns)
        )
        for request in plan.requests
    }


def test_terminal_completed_chunk_with_incomplete_catalog_is_reopened_and_repaired(tmp_path):
    calendar = TradingCalendar()
    windows = (
        FNORolloverWindow("AAA", "STOCK_FUTURE", "101", date(2026, 1, 29), date(2026, 1, 29)),
        FNORolloverWindow("AAA", "STOCK_FUTURE", "202", date(2026, 1, 30), date(2026, 1, 30)),
    )
    interval_ns = 60_000_000_000
    max_request_ns = 86_400_000_000_000
    catalog_path = str(tmp_path / "catalog.sqlite")
    job_store_path = str(tmp_path / "jobs.sqlite")

    catalog = HistoricalCatalog(catalog_path)
    job_store = HistoricalJobStore(job_store_path)
    plan = build_continuous_futures_acquisition_plan(
        windows,
        source="angelone",
        timeframe="1m",
        interval_ns=interval_ns,
        calendar=calendar,
        max_request_ns=max_request_ns,
    )
    records = _records(plan, interval_ns=interval_ns)
    metadata = tuple(
        {
            "source": request.source,
            "instrument": request.instrument,
            "timeframe": request.timeframe,
            "start_ns": request.start_ns,
            "end_ns": request.end_ns,
        }
        for request in plan.requests
    )

    job_store.create(
        job_id="terminal-reconcile-job",
        run_id="continuous-run",
        plan_fingerprint=job_store.fingerprint(metadata),
        total_chunks=len(plan.requests),
        plan_metadata=metadata,
    )

    # Simulate a bad terminal ledger state: chunk 0 says completed, but its
    # catalog is missing the last three candles. Chunk 1 is fully complete.
    first_records = records[plan.requests[0]]
    for record in first_records[:-3]:
        catalog.ingest(record)
    job_store.start_chunk("terminal-reconcile-job", 0)
    job_store.complete_chunk("terminal-reconcile-job", 0)

    for record in records[plan.requests[1]]:
        catalog.ingest(record)
    job_store.start_chunk("terminal-reconcile-job", 1)
    job_store.complete_chunk("terminal-reconcile-job", 1)

    assert job_store.chunk_state("terminal-reconcile-job", 0)[0] == "completed"
    assert job_store.chunk_state("terminal-reconcile-job", 1)[0] == "completed"

    catalog.close()
    job_store.close()

    catalog = HistoricalCatalog(catalog_path)
    job_store = HistoricalJobStore(job_store_path)
    source = RecordingSource(records)
    executor = ResumableHistoricalExecutor(
        HistoricalIngestionService(catalog),
        collect_results=False,
        sleep=lambda _: None,
    )

    resumed = acquire_continuous_futures_history(
        catalog,
        source,
        windows,
        source_name="angelone",
        timeframe="1m",
        interval_ns=interval_ns,
        calendar=calendar,
        max_request_ns=max_request_ns,
        executor=executor,
        job_store=job_store,
        job_id="terminal-reconcile-job",
        run_id="continuous-run",
    )

    assert resumed.completed
    assert len(source.calls) == 1
    repaired_request = source.calls[0]
    original_request = plan.requests[0]
    assert repaired_request.instrument == "NFO:101"
    assert repaired_request.start_ns == original_request.end_ns - 2 * interval_ns
    assert repaired_request.end_ns == original_request.end_ns
    assert job_store.chunk_state("terminal-reconcile-job", 0)[0] == "completed"
    assert job_store.chunk_state("terminal-reconcile-job", 1)[0] == "completed"
    assert job_store.pending_indices("terminal-reconcile-job") == ()
    assert catalog.count(source="angelone", instrument="NFO:101", timeframe="1m") == len(first_records)
    assert catalog.count(source="angelone", instrument="NFO:202", timeframe="1m") == len(records[plan.requests[1]])

    catalog.close()
    job_store.close()
