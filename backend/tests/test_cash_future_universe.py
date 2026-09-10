from datetime import date

from app.backtesting.cash_future_universe import build_cash_future_stock_universe
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord


def test_stock_universe_enumerates_every_expiry_separately_and_excludes_expired():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 9, 1), [
        ContractRecord("NFO", "ABCSEP", "101", date(2026, 9, 24), "STOCK_FUTURE", "ABC", 125),
        ContractRecord("NFO", "ABCOCT", "102", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 125),
        ContractRecord("NFO", "XYZOCT", "201", date(2026, 10, 29), "STOCK_FUTURE", "XYZ", 50),
        ContractRecord("NFO", "ABCOPT", "301", date(2026, 10, 29), "STOCK_OPTION", "ABC", 125),
        ContractRecord("NSE", "ABC-EQ", "401", date(2026, 10, 29), "EQUITY", "ABC", 1),
    ])

    universe = build_cash_future_stock_universe(
        catalog, snapshot_date=date(2026, 9, 1), as_of=date(2026, 9, 25)
    )

    assert [(item.underlying, item.contract_month, item.future_token) for item in universe] == [
        ("ABC", "2026-10", "102"),
        ("XYZ", "2026-10", "201"),
    ]
    assert [item.lot_size for item in universe] == [125, 50]
    catalog.close()


def test_universe_is_deterministically_sorted():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 9, 1), [
        ContractRecord("NFO", "Z2", "2", date(2026, 11, 26), "STOCK_FUTURE", "Z", 10),
        ContractRecord("NFO", "A2", "4", date(2026, 10, 29), "STOCK_FUTURE", "A", 20),
        ContractRecord("NFO", "A1", "3", date(2026, 9, 24), "STOCK_FUTURE", "A", 20),
    ])

    universe = build_cash_future_stock_universe(catalog, snapshot_date=date(2026, 9, 1))
    assert [(x.underlying, x.contract_month) for x in universe] == [
        ("A", "2026-09"), ("A", "2026-10"), ("Z", "2026-11")
    ]
    catalog.close()
