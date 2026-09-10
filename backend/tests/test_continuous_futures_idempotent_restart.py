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


def test_repeated_restart_after_repair_is_idempotent(tmp_path):
    calendar = TradingCalendar()
    windows = (
        FNORolloverWindow("AAA", "STOCK_FUTURE", "101", date(2026, 1, 29), date(2026, 1, 29)),
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
    records_by_request = {
        request: tuple(
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
        for request in plan.requests
    }
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
        job_id="idempotent-restart-job",
        run_id="continuous-run",
        plan_fingerprint=job_store.fingerprint(metadata),
        total_chunks=len(plan.requests),
        plan_metadata=metadata,
    )

    # Leave one internal gap in a terminal chunk so the first restart performs repair.
    missing = {5, 6, 100, 101}
    for index, record in enumerate(records_by_request[plan.requests[0]]):
        if index not in missing:
            catalog.ingest(record)
    for chunk_index in range(len(plan.requests)):
        if chunk_index == 0:
            job_store.start_chunk("idempotent-restart-job", chunk_index)
            job_store.complete_chunk("idempotent-restart-job", chunk_index)
        else:
            # Keep later chunks absent from the catalog/ledger so the normal durable
            # acquisition completes them during the first restart.
            pass

    catalog.close()
    job_store.close()

    catalog = HistoricalCatalog(catalog_path)
    job_store = HistoricalJobStore(job_store_path)
    first_source = RecordingSource(records_by_request)
    first_executor = ResumableHistoricalExecutor(
        HistoricalIngestionService(catalog),
        collect_results=False,
        sleep=lambda _: None,
    )
    first = acquire_continuous_futures_history(
        catalog,
        first_source,
        windows,
        source_name="angelone",
        timeframe="1m",
        interval_ns=interval_ns,
        calendar=calendar,
        max_request_ns=max_request_ns,
        executor=first_executor,
        job_store=job_store,
        job_id="idempotent-restart-job",
        run_id="continuous-run",
    )
    assert first.completed
    assert len(first_source.calls) == len(plan.requests) + 1
    assert job_store.pending_indices("idempotent-restart-job") == ()
    first_counts = {
        request.instrument: catalog.count(
            source="angelone", instrument=request.instrument, timeframe="1m"
        )
        for request in plan.requests
    }
    catalog.close()
    job_store.close()

    # Simulate another full process restart after the repair is already complete.
    catalog = HistoricalCatalog(catalog_path)
    job_store = HistoricalJobStore(job_store_path)
    second_source = RecordingSource(records_by_request)
    second_executor = ResumableHistoricalExecutor(
        HistoricalIngestionService(catalog),
        collect_results=False,
        sleep=lambda _: None,
    )
    second = acquire_continuous_futures_history(
        catalog,
        second_source,
        windows,
        source_name="angelone",
        timeframe="1m",
        interval_ns=interval_ns,
        calendar=calendar,
        max_request_ns=max_request_ns,
        executor=second_executor,
        job_store=job_store,
        job_id="idempotent-restart-job",
        run_id="continuous-run",
    )

    assert second.completed
    assert second_source.calls == []
    assert job_store.pending_indices("idempotent-restart-job") == ()
    assert [
        job_store.chunk_state("idempotent-restart-job", index)[0]
        for index in range(len(plan.requests))
    ] == ["completed"] * len(plan.requests)
    assert {
        request.instrument: catalog.count(
            source="angelone", instrument=request.instrument, timeframe="1m"
        )
        for request in plan.requests
    } == first_counts

    catalog.close()
    job_store.close()
