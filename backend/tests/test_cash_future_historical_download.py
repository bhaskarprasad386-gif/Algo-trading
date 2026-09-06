from datetime import date, datetime, timezone

from app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalRecord


class FakeSource:
    def __init__(self, fail_instrument=None):
        self.fail_instrument = fail_instrument
        self.calls = []

    def fetch(self, request):
        self.calls.append(request)
        if request.instrument == self.fail_instrument:
            raise RuntimeError("temporary provider failure")
        yield HistoricalRecord(
            request.source,
            request.instrument,
            request.timeframe,
            request.start_ns,
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        )


def _contracts():
    contracts = ContractMasterCatalog()
    contracts.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    contracts.upsert_snapshot(date(2026, 1, 30), [
        ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINMAR", "103", date(2026, 3, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    return contracts


def _run_kwargs():
    return dict(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 2, 2, tzinfo=timezone.utc),
        timeframe="1m",
        mode="CURRENT",
        retry_attempts=1,
    )


def test_downloader_persists_spot_and_exact_rollover_contracts():
    catalog = HistoricalCatalog()
    source = FakeSource()
    report = CashFutureHistoricalDownloadService(catalog, _contracts(), source=source).run(**_run_kwargs())
    assert report.completed
    assert len(report.future_executions) == 2
    assert [x.request.instrument for x in report.queue.futures] == ["NFO:101:SBINJAN", "NFO:102:SBINFEB"]
    assert catalog.count() == 3
    assert report.processed_chunks == 3


def test_partial_provider_failure_can_resume_without_duplicate_rows():
    catalog = HistoricalCatalog()
    contracts = _contracts()
    failing = FakeSource(fail_instrument="NFO:101:SBINJAN")
    first = CashFutureHistoricalDownloadService(catalog, contracts, source=failing).run(**_run_kwargs())
    assert not first.completed
    assert first.spot_execution.failed_request_index is None
    assert len(first.future_executions) == 1
    assert first.future_executions[0].failed_request_index == 0
    assert catalog.count() == 1

    recovered_source = FakeSource()
    recovered = CashFutureHistoricalDownloadService(catalog, contracts, source=recovered_source).run(**_run_kwargs())
    assert recovered.completed
    assert catalog.count() == 3


def test_complete_chunks_can_be_skipped_and_reported():
    catalog = HistoricalCatalog()
    source = FakeSource()
    service = CashFutureHistoricalDownloadService(catalog, _contracts(), source=source)
    report = service.run(**_run_kwargs(), should_skip=lambda request: True)
    assert report.completed
    assert report.completed_chunks == 0
    assert report.skipped_chunks == 3
    assert report.processed_chunks == 3
    assert source.calls == []


def test_request_planner_keeps_chunks_non_overlapping():
    request_type = type(
        "R",
        (),
        {
            "source": "angelone",
            "instrument": "NSE:3045:SBIN",
            "timeframe": "1m",
            "start_ns": 0,
            "end_ns": 14 * 24 * 60 * 60 * 1_000_000_000,
        },
    )
    plan = CashFutureHistoricalDownloadService._plan_for_request(request_type())
    assert len(plan.requests) == 3
    for previous, current in zip(plan.requests, plan.requests[1:]):
        assert previous.end_ns + 1 == current.start_ns
