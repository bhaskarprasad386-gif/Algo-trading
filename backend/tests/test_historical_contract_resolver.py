from datetime import date

import pytest

from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_contract_resolver import HistoricalContractResolver


def test_resolves_exact_expiry_month_instead_of_current_near():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(
        date(2026, 9, 7),
        [
            ContractRecord("NFO", "ABC26SEP", "101", date(2026, 9, 24), "STOCK_FUTURE", "ABC", 100),
            ContractRecord("NFO", "ABC26OCT", "102", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 100),
            ContractRecord("NFO", "ABC26NOV", "103", date(2026, 11, 26), "STOCK_FUTURE", "ABC", 100),
        ],
    )
    resolver = HistoricalContractResolver(catalog)

    selected = resolver.resolve_future(
        underlying="abc",
        contract_month="2026-10",
        as_of=date(2026, 9, 10),
    )

    assert selected.token == "102"
    assert selected.symbol == "ABC26OCT"
    assert selected.lot_size == 100
    catalog.close()


def test_missing_expiry_month_fails_closed():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(
        date(2026, 9, 7),
        [ContractRecord("NFO", "ABC26SEP", "101", date(2026, 9, 24), "STOCK_FUTURE", "ABC", 100)],
    )
    resolver = HistoricalContractResolver(catalog)

    with pytest.raises(LookupError):
        resolver.resolve_future_token(
            underlying="ABC",
            contract_month="2026-12",
            as_of=date(2026, 9, 10),
        )
    catalog.close()


def test_invalid_contract_month_fails_closed():
    catalog = ContractMasterCatalog()
    resolver = HistoricalContractResolver(catalog)
    with pytest.raises(ValueError):
        resolver.resolve_future_token(
            underlying="ABC",
            contract_month="SEP-2026",
            as_of=date(2026, 9, 10),
        )
    catalog.close()
