from datetime import date, datetime, timezone

from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.session_gap_planner import SessionWindow


class FakeHistoricalSource:
    def __init__(self):
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        timestamp = request.start_ns
        while timestamp <= request.end_ns:
            yield HistoricalRecord(
                request.source,
                request.instrument,
                request.timeframe,
                timestamp,
                {"close": 100.0},
            )
            timestamp += 60 * 1_000_000_000


def _contract_catalog():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    return catalog


def test_acquisition_repairs_cash_and_current_next_future_without_retaining_results(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(catalog)
    source = FakeHistoricalSource()
    service = CashFutureHistoricalAcquisitionService(
        ingestion,
        source,
        interval_ns=60 * 1_000_000_000,
        max_request_ns=2 * 60 * 1_000_000_000,
        sleep=lambda _: None,
    )

    contract_catalog = _contract_catalog()
    # The acquisition service resolves contracts from the ingestion catalog, so seed the
    # same dated contract snapshot there before planning the queue.
    ingestion.catalog = contract_catalog  # type: ignore[assignment]
    spot_sessions = (SessionWindow(1_000_000_000, 181_000_000_000),)
    future_sessions = {
        "NFO:101:SBINJAN": spot_sessions,
        "NFO:102:SBINFEB": spot_sessions,
    }

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=spot_sessions,
        future_sessions=future_sessions,
        timeframe="1m",
        mode="BOTH",
        retry_attempts=1,
    )

    assert result.execution.failed_request_index is None
    assert result.execution.results == ()
    assert result.execution.completed_chunks == result.execution.processed_chunks
    assert result.plan.requests
    assert {request.instrument for request in result.plan.requests} <= {
        "NSE:3045:SBIN", "NFO:101:SBINJAN", "NFO:102:SBINFEB"
    }
    assert len(source.requests) == len(result.plan.requests)


def test_prepare_is_idempotent_when_catalog_already_covers_expected_timestamps(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(catalog)
    source = FakeHistoricalSource()
    contract_catalog = _contract_catalog()
    ingestion.catalog = contract_catalog  # type: ignore[assignment]
    service = CashFutureHistoricalAcquisitionService(
        ingestion,
        source,
        interval_ns=60 * 1_000_000_000,
        max_request_ns=10 * 60 * 1_000_000_000,
    )
    start = datetime(2026, 1, 29, tzinfo=timezone.utc)
    end = datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc)
    session = SessionWindow(1_000_000_000, 181_000_000_000)

    queue, plan = service.prepare(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=start,
        end=end,
        spot_sessions=(session,),
        future_sessions={
            "NFO:101:SBINJAN": (session,),
            "NFO:102:SBINFEB": (session,),
        },
        mode="BOTH",
    )

    assert queue.spot.instrument == "NSE:3045:SBIN"
    assert plan.requests
