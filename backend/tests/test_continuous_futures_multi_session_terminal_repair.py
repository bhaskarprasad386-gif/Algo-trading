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
        for original, records in self.records_by_request.items():
            if (
                original.source == request.source
                and original.instrument == request.instrument
                and original.timeframe == request.timeframe
            ):
                yield from (
                    record
                    for record in records
                    if request.start_ns <= record.timestamp_ns <= request.end_ns
                )
                return
        raise KeyError(request)


def test_multiple_terminal_chunks_across_rollover_repair_only_corrupt_ranges(tmp_path):
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
    records_by_request = {}
    for request in plan.requests:
        records_by_request[request] = tuple(
            HistoricalRecord(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
                timestamp_ns=timestamp,
                payload={"close": float(index)},
            )
            for index, timestamp in enumerate(
                range(request.start_ns, request.end_ns + 1, interval_ns)
            )
        )

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
        job_id="multi-session-repair",
        run_id="continuous-run",
        plan_fingerprint=job_store.fingerprint(metadata),
        total_chunks=len(plan.requests),
        plan_metadata=metadata,
    )

    # Chunk 0 is fully intact. Chunk 1 is terminal in the ledger but has an
    # internal catalog gap that must be repaired independently.
    for chunk_index, request in enumerate(plan.requests):
        missing = {5, 6} if chunk_index == 0 else {100, 101}
        for index, record in enumerate(records_by_request[request]):
            if index not in missing:
                catalog.ingest(record)
        job_store.start_chunk("multi-session-repair", chunk_index)
        job_store.complete_chunk("multi-session-repair", chunk_index)

    # Restore chunk 0 to complete data: it must never be downloaded on restart.
    for record in records_by_request[plan.requests[0]][5:7]:
        catalog.ingest(record)

    catalog.close()
    job_store.close()

    catalog = HistoricalCatalog(catalog_path)
    job_store = HistoricalJobStore(job_store_path)
    source = RecordingSource(records_by_request)
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
        job_id="multi-session-repair",
        run_id="continuous-run",
    )

    assert resumed.completed
    assert len(source.calls) == 1
    assert (source.calls[0].start_ns, source.calls[0].end_ns) == (
        records_by_request[plan.requests[1]][100].timestamp_ns,
        records_by_request[plan.requests[1]][101].timestamp_ns,
    )
    assert job_store.pending_indices("multi-session-repair") == ()
    assert all(
        job_store.chunk_state("multi-session-repair", index)[0] == "completed"
        for index in range(len(plan.requests))
    )
    for request in plan.requests:
        assert catalog.count(
            source="angelone", instrument=request.instrument, timeframe="1m"
        ) == len(records_by_request[request])

    catalog.close()
    job_store.close()
