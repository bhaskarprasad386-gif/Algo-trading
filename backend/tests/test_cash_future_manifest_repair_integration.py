from datetime import date, datetime, timezone

from app.backtesting.cash_future_coverage_manifest import CoverageRange, build_coverage_manifest
from app.backtesting.cash_future_coverage_manifest_store import CashFutureCoverageManifestStore
from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.session_gap_planner import SessionWindow


def _service(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(catalog)
    contracts = ContractMasterCatalog()
    contracts.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
    ])
    service = CashFutureHistoricalAcquisitionService(
        ingestion, object(), contracts,
        interval_ns=60 * 1_000_000_000,
        max_request_ns=2 * 60 * 1_000_000_000,
    )
    return service, catalog


def _session():
    start = int(datetime(2026, 1, 29, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    return SessionWindow(start, start + 2 * 60 * 1_000_000_000)


def _store_with_range(tmp_path, *, complete):
    store = CashFutureCoverageManifestStore(tmp_path / "coverage.db")
    store.upsert(
        build_coverage_manifest(
            source="angelone",
            ranges=(CoverageRange("NSE:3045:SBIN", _session().start_ns, _session().end_ns, 3,
                                  3 if complete else 2, 0 if complete else 1, complete),),
        ),
        timeframe="1m",
    )
    return store


def test_complete_manifest_skips_provider_repair_even_when_catalog_has_a_gap(tmp_path):
    service, catalog = _service(tmp_path)
    session = _session()
    catalog.ingest([HistoricalRecord("angelone", "NSE:3045:SBIN", "1m", session.start_ns, {"close": 100.0})])
    store = _store_with_range(tmp_path, complete=True)

    _, plan = service.prepare(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, 9, 15, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 9, 17, tzinfo=timezone.utc),
        spot_sessions=(session,), future_sessions={"NFO:101:SBINJAN": (session,)},
        mode="CURRENT", coverage_store=store,
    )

    assert plan.requests == ()


def test_incomplete_manifest_allows_only_unfinished_instrument_into_provider_plan(tmp_path):
    service, catalog = _service(tmp_path)
    session = _session()
    catalog.ingest([HistoricalRecord("angelone", "NSE:3045:SBIN", "1m", session.start_ns, {"close": 100.0})])
    store = _store_with_range(tmp_path, complete=False)

    _, plan = service.prepare(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, 9, 15, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 9, 17, tzinfo=timezone.utc),
        spot_sessions=(session,), future_sessions={"NFO:101:SBINJAN": (session,)},
        mode="CURRENT", coverage_store=store,
    )

    assert plan.requests
    assert {request.instrument for request in plan.requests} == {"NSE:3045:SBIN"}


def test_repair_plan_is_idempotent_after_manifest_becomes_complete(tmp_path):
    store = _store_with_range(tmp_path, complete=False)
    assert store.repair_plan(source="angelone", timeframe="1m")
    store.upsert(
        build_coverage_manifest(
            source="angelone",
            ranges=(CoverageRange("NSE:3045:SBIN", _session().start_ns, _session().end_ns, 3, 3, 0, True),),
        ),
        timeframe="1m",
    )
    assert store.repair_plan(source="angelone", timeframe="1m") == ()
