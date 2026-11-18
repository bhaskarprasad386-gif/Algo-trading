from datetime import date, datetime, timezone

from app.backtesting.calendar_spread_historical_loader import (
    CalendarSpreadHistoricalLoader,
    CalendarSpreadHistorySelection,
)
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def _seed(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "data.sqlite"))
    contracts = ContractMasterCatalog(str(tmp_path / "contracts.sqlite"))
    snapshot = date(2026, 9, 24)
    contracts.upsert_snapshot(snapshot, [
        ContractRecord("NFO", "SBIN30SEP2026FUT", "1001", date(2026, 9, 30), "STOCK_FUTURE", "SBIN", 150, snapshot),
        ContractRecord("NFO", "SBIN29OCT2026FUT", "1002", date(2026, 10, 29), "STOCK_FUTURE", "SBIN", 150, snapshot),
    ])
    base = int(datetime(2026, 9, 24, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    records = [
        HistoricalRecord("angelone", "NFO:1001:SBIN30SEP2026FUT", "1m", base, {"bid": 100, "ask": 101}),
        HistoricalRecord("angelone", "NFO:1002:SBIN29OCT2026FUT", "1m", base, {"bid": 104, "ask": 105}),
        HistoricalRecord("angelone", "NFO:1001:SBIN30SEP2026FUT", "1m", base + 60_000_000_000, {"bid": 101, "ask": 102}),
        HistoricalRecord("angelone", "NFO:1002:SBIN29OCT2026FUT", "1m", base + 60_000_000_000, {"bid": 105, "ask": 106}),
    ]
    catalog.ingest(records)
    return catalog, contracts


def test_calendar_loader_resolves_point_in_time_near_far_and_lot_size(tmp_path):
    catalog, contracts = _seed(tmp_path)
    try:
        selection = CalendarSpreadHistorySelection(
            underlying="SBIN",
            exchange="NFO",
            start_date=date(2026, 9, 24),
            end_date=date(2026, 9, 24),
            near_contract_month="2026-09",
            far_contract_month="2026-10",
        )
        points = CalendarSpreadHistoricalLoader(catalog, contracts).load_points(selection)
        assert len(points) == 2
        assert points[0].near_expiry == date(2026, 9, 30)
        assert points[0].far_expiry == date(2026, 10, 29)
        assert points[0].lot_size == 150
        assert points[0].near_bid == 100
        assert points[0].far_ask == 105
    finally:
        catalog.close()
        contracts.close()


def test_calendar_loader_rejects_invalid_month_order():
    try:
        CalendarSpreadHistorySelection(
            underlying="SBIN",
            exchange="NFO",
            start_date=date(2026, 9, 24),
            end_date=date(2026, 9, 24),
            near_contract_month="2026-10",
            far_contract_month="2026-09",
        )
    except ValueError as exc:
        assert "far_contract_month" in str(exc)
    else:
        raise AssertionError("expected invalid contract-month ordering")


def test_calendar_loader_requires_matching_lot_sizes(tmp_path):
    catalog, contracts = _seed(tmp_path)
    contracts.close()
    contracts = ContractMasterCatalog(str(tmp_path / "contracts.sqlite"))
    snapshot = date(2026, 9, 24)
    contracts.upsert_snapshot(snapshot, [
        ContractRecord("NFO", "SBIN30SEP2026FUT", "1001", date(2026, 9, 30), "STOCK_FUTURE", "SBIN", 150, snapshot),
        ContractRecord("NFO", "SBIN29OCT2026FUT", "1002", date(2026, 10, 29), "STOCK_FUTURE", "SBIN", 300, snapshot),
    ])
    try:
        selection = CalendarSpreadHistorySelection(
            underlying="SBIN", exchange="NFO",
            start_date=snapshot, end_date=snapshot,
            near_contract_month="2026-09", far_contract_month="2026-10",
        )
        loader = CalendarSpreadHistoricalLoader(catalog, contracts)
        try:
            next(loader.iter_points(selection))
        except ValueError as exc:
            assert "lot sizes differ" in str(exc)
        else:
            raise AssertionError("expected lot-size mismatch")
    finally:
        catalog.close()
        contracts.close()
