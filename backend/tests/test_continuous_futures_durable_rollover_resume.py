from datetime import date

from app.backtesting.continuous_futures_acquisition import acquire_continuous_futures_history
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.trading_calendar import TradingCalendar


class RecordingSource:
    def __init__(self, records_by_request, *, fail_once_instrument=None):
        self.records_by_request = records_by_request
        self.fail_once_instrument = fail_once_instrument
        self.failed = False
        self.calls = []

    def fetch(self, request: HistoricalFetchRequest):
        self.calls.append(request)
        if self.fail_once_instrument == request.instrument and not self.failed:
            self.failed = True
            raise RuntimeError("simulated provider interruption")
        yield from self.records_by_request[request]


def _records(plan, *, interval_ns):
    records = {}
    for request in plan.requests:
        timestamps = range(request.start_ns, request.end_ns + 1, interval_ns)
        records[request] = tuple(
            HistoricalRecord(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
                timestamp_ns=timestamp,
                payload={"close": 100.0},
            )
            for timestamp in timestamps
        )
    return records


def test_durable_rollover_resume_skips_completed_old_contract_and_resumes_new_contract():
    calendar = TradingCalendar()
    windows = (
        FNORolloverWindow("AAA", "STOCK_FUTURE", "101", date(2026, 1, 28), date(2026, 1, 29)),
        FNORolloverWindow("AAA", "STOCK_FUTURE", "202", date(2026, 1, 30), date(2026, 2, 2)),
    )
    interval_ns = 60_000_000_000
    max_request_ns = 86_400_000_000_000

    catalog = HistoricalCatalog()
    job_store = HistoricalJobStore()
    executor = ResumableHistoricalExecutor(
        HistoricalIngestionService(catalog),
        collect_results=False,
        sleep=lambda _: None,
    )

    from app.backtesting.continuous_futures_acquisition import build_continuous_futures_acquisition_plan

    plan = build_continuous_futures_acquisition_plan(
        windows,
        source="angelone",
        timeframe="1m",
        interval_ns=interval_ns,
        calendar=calendar,
        max_request_ns=max_request_ns,
    )
    records = _records(plan, interval_ns=interval_ns)
    source = RecordingSource(records, fail_once_instrument="NFO:202")

    first = acquire_continuous_futures_history(
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
        job_id="rollover-job",
        run_id="run-1",
    )

    assert first.execution.failed_request_index == 2
    assert [request.instrument for request in source.calls] == [
        "NFO:101",
        "NFO:101",
        "NFO:202",
    ]
    assert job_store.pending_indices("rollover-job") == (2, 3, 4)

    second = acquire_continuous_futures_history(
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
        job_id="rollover-job",
        run_id="run-1",
    )

    assert second.completed
    assert [request.instrument for request in source.calls] == [
        "NFO:101",
        "NFO:101",
        "NFO:202",
        "NFO:202",
        "NFO:202",
    ]
    assert job_store.pending_indices("rollover-job") == ()
    assert job_store.get("rollover-job").state == "completed"
    assert catalog.count(source="angelone", timeframe="1m") == sum(len(items) for items in records.values())

    catalog.close()
    job_store.close()
