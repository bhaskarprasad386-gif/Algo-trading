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


class RestartableSource:
    def __init__(self, records_by_request, *, fail_instrument=None):
        self.records_by_request = records_by_request
        self.fail_instrument = fail_instrument
        self.calls = []

    def fetch(self, request):
        self.calls.append(request)
        if request.instrument == self.fail_instrument:
            raise RuntimeError("simulated process interruption")
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


def test_persistent_rollover_resume_recovers_running_chunk_after_process_crash(tmp_path):
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

    first_executor = ResumableHistoricalExecutor(
        HistoricalIngestionService(catalog),
        collect_results=False,
        sleep=lambda _: None,
    )
    first_source = RestartableSource(records, fail_instrument="NFO:202")

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
        job_id="restart-rollover-job",
        run_id="process-1",
    )

    assert not first.completed
    assert job_store.pending_indices("restart-rollover-job") == (1,)
    assert [request.instrument for request in first_source.calls] == [
        "NFO:101",
        "NFO:202",
        "NFO:202",
        "NFO:202",
    ]
    old_count = catalog.count(source="angelone", instrument="NFO:101", timeframe="1m")
    assert old_count == len(records[plan.requests[0]])

    # Simulate a worker dying after marking the pending chunk as running.
    job_store.start_chunk("restart-rollover-job", 1)
    assert job_store.chunk_state("restart-rollover-job", 1)[0] == "running"
    catalog.close()
    job_store.close()

    # A fresh process must recover the durable running state before resuming.
    catalog = HistoricalCatalog(catalog_path)
    job_store = HistoricalJobStore(job_store_path)
    assert job_store.chunk_state("restart-rollover-job", 1)[0] == "running"

    second_executor = ResumableHistoricalExecutor(
        HistoricalIngestionService(catalog),
        collect_results=False,
        sleep=lambda _: None,
    )
    second_source = RestartableSource(records)

    resumed = acquire_continuous_futures_history(
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
        job_id="restart-rollover-job",
        run_id="process-1",
    )

    assert resumed.completed
    assert [request.instrument for request in second_source.calls] == ["NFO:202"]
    assert job_store.pending_indices("restart-rollover-job") == ()
    assert job_store.get("restart-rollover-job").state == "completed"
    assert catalog.count(source="angelone", instrument="NFO:101", timeframe="1m") == old_count
    assert catalog.count(source="angelone", timeframe="1m") == sum(
        len(items) for items in records.values()
    )

    catalog.close()
    job_store.close()
