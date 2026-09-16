from datetime import date

import pytest

from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord


def _master() -> ContractMasterCatalog:
    catalog = ContractMasterCatalog()
    snapshot = date(2026, 9, 7)
    catalog.upsert_snapshot(
        snapshot,
        [
            ContractRecord("NFO", "ABC26SEP", "101", date(2026, 9, 24), "STOCK_FUTURE", "ABC", 100),
            ContractRecord("NFO", "ABC26OCT", "102", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 100),
            ContractRecord("NFO", "ABC26NOV", "103", date(2026, 11, 26), "STOCK_FUTURE", "ABC", 100),
        ],
    )
    return catalog


def test_current_and_near_are_expiry_ordered_for_replay_date():
    catalog = _master()
    assert catalog.resolve(exchange="NFO", underlying="ABC", as_of=date(2026, 9, 7), mode="CURRENT").token == "101"
    assert catalog.resolve(exchange="NFO", underlying="ABC", as_of=date(2026, 9, 7), mode="NEAR").token == "102"
    catalog.close()


def test_same_expiry_contracts_are_resolved_deterministically_by_token():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(
        date(2026, 9, 7),
        [
            ContractRecord("NFO", "ABC26SEP-B", "102", date(2026, 9, 24), "STOCK_FUTURE", "ABC", 100),
            ContractRecord("NFO", "ABC26SEP-A", "101", date(2026, 9, 24), "STOCK_FUTURE", "ABC", 100),
            ContractRecord("NFO", "ABC26OCT", "103", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 100),
        ],
    )
    assert catalog.resolve(exchange="NFO", underlying="ABC", as_of=date(2026, 9, 7), mode="CURRENT").token == "101"
    assert catalog.resolve(exchange="NFO", underlying="ABC", as_of=date(2026, 9, 7), mode="NEAR").token == "102"
    catalog.close()


def test_expired_contract_is_not_selected_after_expiry():
    catalog = _master()
    assert catalog.resolve(exchange="NFO", underlying="ABC", as_of=date(2026, 9, 25), mode="CURRENT").token == "102"
    catalog.close()


def test_no_historical_contract_means_fail_closed():
    catalog = ContractMasterCatalog()
    with pytest.raises(LookupError):
        catalog.resolve(exchange="NFO", underlying="ABC", as_of=date(2026, 9, 7), mode="CURRENT")
    catalog.close()


def test_index_futures_are_not_returned_by_stock_future_resolver():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(
        date(2026, 9, 7),
        [ContractRecord("NFO", "NIFTY26SEP", "201", date(2026, 9, 24), "INDEX_FUTURE", "NIFTY", 65)],
    )
    with pytest.raises(LookupError):
        catalog.resolve(exchange="NFO", underlying="NIFTY", as_of=date(2026, 9, 7), mode="CURRENT")
    catalog.close()


def test_upsert_rejects_records_from_multiple_snapshot_dates():
    catalog = ContractMasterCatalog()
    records = [
        ContractRecord("NFO", "ABC26SEP", "101", date(2026, 9, 24), "STOCK_FUTURE", "ABC", 100, date(2026, 9, 7)),
        ContractRecord("NFO", "ABC26OCT", "102", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 100, date(2026, 9, 8)),
    ]
    with pytest.raises(ValueError, match="one snapshot_date"):
        catalog.upsert(records)
    assert catalog.snapshot_dates() == ()
    catalog.close()


def test_contract_month_requires_exact_yyyy_mm_format():
    catalog = _master()
    for value in ("2026-9", "20269", "2026/09", "2026-090"):
        with pytest.raises(ValueError, match="contract_month must be YYYY-MM"):
            catalog.resolve_contract_month(
                exchange="NFO",
                underlying="ABC",
                contract_month=value,
                as_of=date(2026, 9, 7),
            )
    catalog.close()


def test_contract_month_resolution_is_deterministic_for_same_expiry():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(
        date(2026, 9, 7),
        [
            ContractRecord("NFO", "ABC26SEP-B", "102", date(2026, 9, 24), "STOCK_FUTURE", "ABC", 100),
            ContractRecord("NFO", "ABC26SEP-A", "101", date(2026, 9, 24), "STOCK_FUTURE", "ABC", 100),
        ],
    )
    assert catalog.resolve_contract_month(
        exchange="NFO", underlying="ABC", contract_month="2026-09", as_of=date(2026, 9, 7)
    ).token == "101"
    catalog.close()
