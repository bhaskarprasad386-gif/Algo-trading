from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.backtesting.angelone_historical import AngelOneHistoricalSource
from app.backtesting.cash_future_historical_acquisition import (
    CashFutureHistoricalAcquisitionService,
)
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.backtesting.session_gap_planner import SessionWindow


MARKET_TZ = ZoneInfo("Asia/Kolkata")


class FakeClient:
    def getCandleData(self, params):
        exchange = params["exchange"]
        token = params["symboltoken"]
        return {
            "status": True,
            "data": [
                ["2026-01-05 09:15", 100, 101, 99, 100.5, 1000, 250],
                ["2026-01-05 09:16", 100.5, 102, 100, 101.5, 1200, 275],
            ],
        }


class FakeAuth:
    def __init__(self):
        self.client = FakeClient()

    def get_client(self):
        return self.client


class FakeLimiter:
    def __init__(self):
        self.calls = 0

    def acquire(self):
        self.calls += 1


def _ns(local: datetime) -> int:
    return int(local.replace(tzinfo=MARKET_TZ).timestamp() * 1_000_000_000)


def test_cash_future_acquisition_downloads_spot_and_exact_future_into_catalog():
    catalog = HistoricalCatalog()
    contracts = ContractMasterCatalog()
    ingestion = HistoricalIngestionService(catalog)

    snapshot = date(2026, 1, 5)
    future = ContractRecord(
        exchange="NFO",
        symbol="SBIN26JANFUT",
        token="9001",
        expiry=date(2026, 1, 29),
        instrument_type="STOCK_FUTURE",
        underlying="SBIN",
        lot_size=750,
    )
    contracts.upsert_snapshot(snapshot, [future])

    source = AngelOneHistoricalSource(
        auth=FakeAuth(),
        limiter=FakeLimiter(),
        chunk_days=30,
    )
    service = CashFutureHistoricalAcquisitionService(
        ingestion,
        source,
        contracts,
        interval_ns=60_000_000_000,
        max_request_ns=24 * 60 * 60 * 1_000_000_000,
    )

    start = datetime(2026, 1, 5, 9, 15)
    end = datetime(2026, 1, 5, 9, 16)
    session = SessionWindow(_ns(start), _ns(end))

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN-EQ",
        exchange="NFO",
        underlying="SBIN",
        start=start,
        end=end,
        spot_sessions=(session,),
        future_sessions={"NFO:9001:SBIN26JANFUT": (session,)},
        timeframe="1m",
        mode="CURRENT",
        source="angelone",
        max_repair_passes=2,
    )

    assert result.execution.failed_request_index is None
    assert result.coverage.complete
    assert result.coverage.missing_timestamps == 0
    assert len(catalog.records(source="angelone", instrument="NSE:3045:SBIN-EQ", timeframe="1m")) == 2
    assert len(catalog.records(source="angelone", instrument="NFO:9001:SBIN26JANFUT", timeframe="1m")) == 2

    contracts.close()
    catalog.close()
