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
    def __init__(self, records_by_request, *, fail: bool):
        self.records_by_request = records_by_request
        self.fail = fail
        self.calls = []

    def fetch(self, request):
        self.calls.append(request)
        records = self.records_by_request[request]
        if self.fail:
            raise RuntimeError("simulated process interruption")
        yield from records


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


def test_persistent_rollover_resume_survives_new_process_and_skips_completed_chunks():
    calendar = TradingCalendar()
    windows = (
        FNORolloverWindow("AAA", "STOCK_FUTURE", "101", date(2026, 1, 29), date(2026, 1, 29)),
        FNORolloverWindow("AAA", "STOCK_FUTURE", "202", date(2026, 1, 30), date(2026, 1, 30)),
    )
    interval_ns = 60_000_000_000
    max_request_ns = 86_400_000_000_000

    catalog = HistoricalCatalog()
    job_store = HistoricalJobStore()
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
    first_source = RestartableSource(records, fail=False)

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

    assert first.completed
    assert job_store.pending_indices("restart-rollover-job") == ()
    assert len(first_source.calls) == 2

    # Simulate a completely new process: new catalog/job-store handles and executor.
    catalog.close()
    job_store.close()

    catalog = HistoricalCatalog()
    job_store = HistoricalJobStore()
    second_executor = ResumableHistoricalExecutor(
        HistoricalIngestionService(catalog),
        collect_results=False,
        sleep=lambda _: None,
    )
    second_source = RestartableSource(records, fail=False)

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
        run_id="process-2",
    )

    assert resumed.completed
    assert second_source.calls == []
    assert job_store.pending_indices("restart-rollover-job") == ()
    assert job_store.get("restart-rollover-job").state == "completed"
    assert catalog.count(source="angelone", timeframe="1m") == sum(
        len(items) for items in records.values()
    )

    catalog.close()
    job_store.close()
