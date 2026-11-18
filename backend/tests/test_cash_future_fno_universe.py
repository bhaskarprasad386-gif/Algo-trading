from datetime import date

from app.backtesting.cash_future_universe import build_cash_future_fno_universe
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord


def test_fno_universe_separates_stock_cash_path_from_indices():
    catalog = ContractMasterCatalog()
    snapshot = date(2026, 9, 1)
    catalog.upsert_snapshot(snapshot, [
        ContractRecord("NFO", "ABC26OCT", "101", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 125),
        ContractRecord("NFO", "ABC26NOV", "102", date(2026, 11, 26), "STOCK_FUTURE", "ABC", 125),
        ContractRecord("NFO", "XYZ26OCT", "201", date(2026, 10, 29), "STOCK_FUTURE", "XYZ", 50),
        ContractRecord("NFO", "NIFTY26OCT", "301", date(2026, 10, 29), "INDEX_FUTURE", "NIFTY", 75),
        ContractRecord("NFO", "NIFTY26NOV", "302", date(2026, 11, 26), "INDEX_FUTURE", "NIFTY", 75),
        ContractRecord("NFO", "ABCOPT", "401", date(2026, 10, 29), "STOCK_OPTION", "ABC", 125),
    ])

    universe = build_cash_future_fno_universe(catalog, snapshot_date=snapshot)

    assert [(x.underlying, x.contract_month, x.future_token) for x in universe.stocks] == [
        ("ABC", "2026-10", "101"),
        ("ABC", "2026-11", "102"),
        ("XYZ", "2026-10", "201"),
    ]
    assert [(x.underlying, x.contract_month, x.future_token) for x in universe.indices] == [
        ("NIFTY", "2026-10", "301"),
        ("NIFTY", "2026-11", "302"),
    ]
    assert universe.stock_underlyings == ("ABC", "XYZ")
    assert universe.index_underlyings == ("NIFTY",)
    catalog.close()


def test_fno_universe_excludes_expired_contracts_deterministically():
    catalog = ContractMasterCatalog()
    snapshot = date(2026, 9, 1)
    catalog.upsert_snapshot(snapshot, [
        ContractRecord("NFO", "ABCSEP", "1", date(2026, 9, 24), "STOCK_FUTURE", "ABC", 125),
        ContractRecord("NFO", "ABCOCT", "2", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 125),
        ContractRecord("NFO", "NIFTYSEP", "3", date(2026, 9, 24), "INDEX_FUTURE", "NIFTY", 75),
        ContractRecord("NFO", "NIFTYOCT", "4", date(2026, 10, 29), "INDEX_FUTURE", "NIFTY", 75),
    ])

    universe = build_cash_future_fno_universe(catalog, snapshot_date=snapshot, as_of=date(2026, 9, 25))

    assert [(x.contract_month, x.future_token) for x in universe.stocks] == [("2026-10", "2")]
    assert [(x.contract_month, x.future_token) for x in universe.indices] == [("2026-10", "4")]
    catalog.close()
