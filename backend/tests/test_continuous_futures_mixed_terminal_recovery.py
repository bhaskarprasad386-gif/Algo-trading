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
                and original.start_ns <= request.start_ns <= original.end_ns
            ):
                yield from (
                    record
                    for record in records
                    if request.start_ns <= record.timestamp_ns <= request.end_ns
                )
                return
        raise KeyError(request)


def test_mixed_terminal_states_reconcile_only_corrupt_chunks(tmp_path):
    calendar = TradingCalendar()
    windows = (
        FNORolloverWindow("AAA", "STOCK_FUTURE", "101", date(2026, 1, 28), date(2026, 1, 29)),
        FNORolloverWindow("AAA", "STOCK_FUTURE", "202", date(2026, 1, 30), date(2026, 2, 2)),
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
        job_id="mixed-terminal-recovery",
        run_id="continuous-run",
        plan_fingerprint=job_store.fingerprint(metadata),
        total_chunks=len(plan.requests),
        plan_metadata=metadata,
    )

    # 0 = complete, 1 = corrupt completed, 2 = corrupt skipped, 3 = interrupted/running.
    missing_by_chunk = {1: {100, 101}, 2: {200, 201}, 3: {300, 301}}
    for chunk_index, request in enumerate(plan.requests):
        missing = missing_by_chunk.get(chunk_index, set())
        for index, record in enumerate(records_by_request[request]):
            if index not in missing:
                catalog.ingest(record)
        job_store.start_chunk("mixed-terminal-recovery", chunk_index)
        if chunk_index in (0, 1):
            job_store.complete_chunk("mixed-terminal-recovery", chunk_index)
        elif chunk_index == 2:
            job_store.complete_chunk("mixed-terminal-recovery", chunk_index, skipped=True)
        # chunk 3 remains running to model process interruption.

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
        job_id="mixed-terminal-recovery",
        run_id="continuous-run",
    )

    assert resumed.completed
    assert len(source.calls) == 3
    calls_by_instrument = {}
    for request in source.calls:
        calls_by_instrument.setdefault(request.instrument, []).append(
            (request.start_ns, request.end_ns)
        )
    assert calls_by_instrument["NFO:101"] == [
        (records_by_request[plan.requests[1]][100].timestamp_ns,
         records_by_request[plan.requests[1]][101].timestamp_ns)
    ]
    assert calls_by_instrument["NFO:202"] == [
        (records_by_request[plan.requests[2]][200].timestamp_ns,
         records_by_request[plan.requests[2]][201].timestamp_ns),
        (records_by_request[plan.requests[3]][300].timestamp_ns,
         records_by_request[plan.requests[3]][301].timestamp_ns),
    ]
    assert job_store.pending_indices("mixed-terminal-recovery") == ()
    for chunk_index in range(len(plan.requests)):
        assert job_store.chunk_state("mixed-terminal-recovery", chunk_index)[0] == "completed"
    for request in plan.requests:
        assert catalog.count(
            source="angelone", instrument=request.instrument, timeframe="1m"
        ) == len(records_by_request[request])

    catalog.close()
    job_store.close()
