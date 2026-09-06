from datetime import date, datetime, timezone

from app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalRecord


class FakeSource:
    def fetch(self, request):
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


def test_downloader_persists_spot_and_exact_rollover_contracts():
    catalog = HistoricalCatalog()
    service = CashFutureHistoricalDownloadService(catalog, _contracts(), source=FakeSource())
    report = service.run(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 2, 2, tzinfo=timezone.utc),
        timeframe="1m", mode="CURRENT",
    )
    assert report.completed
    assert len(report.future_executions) == 2
    assert [x.request.instrument for x in report.queue.futures] == ["NFO:101:SBINJAN", "NFO:102:SBINFEB"]
    assert catalog.count() == 3


def test_request_planner_keeps_chunks_non_overlapping():
    request = next(iter(CashFutureHistoricalDownloadService._plan_for_request(
        type("R", (), {"source": "angelone", "instrument": "NSE:3045:SBIN", "timeframe": "1m",
                         "start_ns": 0, "end_ns": 14 * 24 * 60 * 60 * 1_000_000_000})()
    ).requests))
    assert request.start_ns == 0
