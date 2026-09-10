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


def test_completed_terminal_chunk_with_internal_catalog_gaps_repairs_only_missing_ranges(tmp_path):
    calendar = TradingCalendar()
    windows = (
        FNORolloverWindow("AAA", "STOCK_FUTURE", "101", date(2026, 1, 29), date(2026, 1, 29)),
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
    request = plan.requests[0]
    records = tuple(
        HistoricalRecord(
            source=request.source,
            instrument=request.instrument,
            timeframe=request.timeframe,
            timestamp_ns=timestamp,
            payload={"close": 100.0},
        )
        for timestamp in range(request.start_ns, request.end_ns + 1, interval_ns)
    )
    metadata = tuple(
        {
            "source": item.source,
            "instrument": item.instrument,
            "timeframe": item.timeframe,
            "start_ns": item.start_ns,
            "end_ns": item.end_ns,
        }
        for item in plan.requests
    )
    job_store.create(
        job_id="internal-gap-job",
        run_id="continuous-run",
        plan_fingerprint=job_store.fingerprint(metadata),
        total_chunks=len(plan.requests),
        plan_metadata=metadata,
    )

    # The terminal ledger says complete, but two separate internal ranges are missing.
    missing_indices = {5, 6, 100, 101}
    for index, record in enumerate(records):
        if index not in missing_indices:
            catalog.ingest(record)
    job_store.start_chunk("internal-gap-job", 0)
    job_store.complete_chunk("internal-gap-job", 0)
    catalog.close()
    job_store.close()

    catalog = HistoricalCatalog(catalog_path)
    job_store = HistoricalJobStore(job_store_path)
    source = RecordingSource({request: records})
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
        job_id="internal-gap-job",
        run_id="continuous-run",
    )

    assert resumed.completed
    assert len(source.calls) == 2
    assert [(call.start_ns, call.end_ns) for call in source.calls] == [
        (records[5].timestamp_ns, records[6].timestamp_ns),
        (records[100].timestamp_ns, records[101].timestamp_ns),
    ]
    assert job_store.chunk_state("internal-gap-job", 0)[0] == "completed"
    assert job_store.pending_indices("internal-gap-job") == ()
    assert catalog.count(source="angelone", instrument="NFO:101", timeframe="1m") == len(records)

    catalog.close()
    job_store.close()
