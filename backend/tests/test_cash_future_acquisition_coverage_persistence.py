from datetime import date, datetime, timezone

from app.backtesting.cash_future_coverage_manifest_store import CashFutureCoverageManifestStore
from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.session_gap_planner import SessionWindow


class FakeHistoricalSource:
    def fetch(self, request):
        timestamp = request.start_ns
        interval = 60 * 1_000_000_000
        while timestamp <= request.end_ns:
            yield HistoricalRecord(
                request.source,
                request.instrument,
                request.timeframe,
                timestamp,
                {"close": 100.0},
            )
            timestamp += interval


def _catalog():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(
        date(2026, 1, 1),
        [
            ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
            ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
        ],
    )
    return catalog


def _session():
    start = int(datetime(2026, 1, 29, 3, 45, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    return SessionWindow(start, start + 3 * 60 * 1_000_000_000)


def test_acquisition_persists_session_scoped_manifest_and_clears_missing_gaps(tmp_path):
    history = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(history)
    service = CashFutureHistoricalAcquisitionService(
        ingestion,
        FakeHistoricalSource(),
        _catalog(),
        interval_ns=60 * 1_000_000_000,
        max_request_ns=2 * 60 * 1_000_000_000,
    )
    manifest_store = CashFutureCoverageManifestStore(tmp_path / "coverage.db")
    session = _session()
    future_sessions = {
        "NFO:101:SBINJAN": (session,),
        "NFO:102:SBINFEB": (session,),
    }

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=(session,),
        future_sessions=future_sessions,
        timeframe="1m",
        mode="BOTH",
        retry_attempts=1,
        coverage_store=manifest_store,
    )

    assert result.coverage.complete
    persisted = manifest_store.ranges(source="angelone")
    assert len(persisted) == 3
    assert all(item.complete for item in persisted)
    assert manifest_store.missing(source="angelone") == ()
    assert {item.instrument for item in persisted} == {
        "NSE:3045:SBIN",
        "NFO:101:SBINJAN",
        "NFO:102:SBINFEB",
    }


def test_manifest_does_not_mark_between_session_time_as_missing(tmp_path):
    history = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(history)
    service = CashFutureHistoricalAcquisitionService(
        ingestion,
        FakeHistoricalSource(),
        _catalog(),
        interval_ns=60 * 1_000_000_000,
        max_request_ns=10 * 60 * 1_000_000_000,
    )
    manifest_store = CashFutureCoverageManifestStore(tmp_path / "coverage.db")
    first = _session()
    second = SessionWindow(first.end_ns + 12 * 60 * 60 * 1_000_000_000, first.end_ns + 12 * 60 * 60 * 1_000_000_000 + 60 * 1_000_000_000)

    service.acquire(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 30, tzinfo=timezone.utc),
        spot_sessions=(first, second),
        future_sessions={"NFO:101:SBINJAN": (first, second)},
        timeframe="1m",
        mode="CURRENT",
        retry_attempts=1,
        coverage_store=manifest_store,
    )

    spot_ranges = manifest_store.ranges(source="angelone", instrument="NSE:3045:SBIN")
    assert len(spot_ranges) == 2
    assert all(item.missing_points == 0 for item in spot_ranges)
