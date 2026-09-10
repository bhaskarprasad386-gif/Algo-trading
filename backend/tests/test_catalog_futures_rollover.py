from datetime import date

from app.backtesting.catalog_futures_rollover import (
    build_catalog_futures_rollover_windows,
    build_catalog_futures_rollover_windows_for_universe,
)
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord


def _catalog() -> ContractMasterCatalog:
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 1, 1), (
        ContractRecord("NFO", "AAA26JANFUT", "101", date(2026, 1, 29), "STOCK_FUTURE", "AAA", 10),
        ContractRecord("NFO", "AAA26FEBFUT", "102", date(2026, 2, 26), "STOCK_FUTURE", "AAA", 10),
        ContractRecord("NFO", "BBB26JANFUT", "201", date(2026, 1, 29), "STOCK_FUTURE", "BBB", 20),
        ContractRecord("NFO", "BAD", "999", date(2026, 1, 29), "OPTION", "AAA", 10),
    ))
    return catalog


def test_catalog_builder_uses_real_contract_tokens_and_expiry_chain():
    catalog = _catalog()
    windows = build_catalog_futures_rollover_windows(
        catalog,
        underlying="AAA",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 2, 28),
    )
    assert [(w.contract_token, w.start_date, w.end_date) for w in windows] == [
        ("101", date(2026, 1, 1), date(2026, 1, 29)),
        ("102", date(2026, 1, 30), date(2026, 2, 28)),
    ]


def test_catalog_universe_is_sorted_and_excludes_other_instrument_types():
    catalog = _catalog()
    windows = build_catalog_futures_rollover_windows_for_universe(
        catalog,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
    )
    assert [(w.underlying, w.contract_token) for w in windows] == [("AAA", "101"), ("BBB", "201")]


def test_explicit_snapshot_is_used_instead_of_latest_snapshot():
    catalog = _catalog()
    catalog.upsert_snapshot(date(2026, 2, 1), (
        ContractRecord("NFO", "AAA26FEBFUT", "302", date(2026, 2, 26), "STOCK_FUTURE", "AAA", 10),
    ))
    windows = build_catalog_futures_rollover_windows(
        catalog,
        underlying="AAA",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 20),
        snapshot_date=date(2026, 1, 1),
    )
    assert windows[0].contract_token == "101"
