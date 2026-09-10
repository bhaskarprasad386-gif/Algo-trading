from datetime import date

from app.backtesting.contract_master import ContractRecord
from app.backtesting.fno_acquisition import build_fno_acquisition_plan
from app.backtesting.fno_historical_executor import (
    FNOHistoricalAcquisitionService,
    to_historical_sync_plan,
)
from app.backtesting.fno_universe import build_fno_universe
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore


class Source:
    def __init__(self, catalog):
        self.catalog = catalog
        self.calls = []

    def fetch(self, request):
        self.calls.append(request)
        yield HistoricalRecord(
            source=request.source,
            instrument=request.instrument,
            timeframe=request.timeframe,
            timestamp_ns=request.start_ns,
            payload={"close": 100},
        )


def make_plan():
    universe = build_fno_universe(
        [
            ContractRecord("NFO", "TCS", "101", date(2026, 9, 24), "STOCK_FUTURE", "TCS", 175),
        ],
        snapshot_date=date(2026, 9, 1),
    )
    return build_fno_acquisition_plan(
        universe,
        as_of=date(2026, 9, 1),
        timeframe="1m",
        start_ns=0,
        end_ns=5,
        max_request_ns=3,
    )


def test_fno_plan_converts_to_common_sync_plan():
    plan = make_plan()
    sync_plan = to_historical_sync_plan(plan, source="provider")
    assert len(sync_plan.requests) == plan.job_count
    assert sync_plan.requests[0].instrument == "101"


def test_fno_acquisition_persists_each_chunk_and_is_restart_safe():
    catalog = HistoricalCatalog()
    store = HistoricalJobStore()
    source = Source(catalog)
    executor = ResumableHistoricalExecutor(HistoricalIngestionService(catalog), collect_results=False)
    service = FNOHistoricalAcquisitionService(executor)
    plan = make_plan()

    first = service.run(
        source,
        plan,
        source_name="provider",
        job_store=store,
        job_id="fno-1",
        run_id="run-1",
    )
    assert first.completed
    assert first.execution.completed_chunks == 2
    assert catalog.count(source="provider", instrument="101", timeframe="1m") == 2
    assert len(source.calls) == 2

    second = service.run(
        source,
        plan,
        source_name="provider",
        job_store=store,
        job_id="fno-1",
        run_id="run-1",
    )
    assert second.completed
    assert second.execution.completed_chunks == 0
    assert len(source.calls) == 2
    assert store.get("fno-1").state == "completed"
