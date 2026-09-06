from datetime import date

from app.backtesting.cash_future_selection import select_cash_future
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord


def _catalog():
    catalog = ContractMasterCatalog()
    catalog.upsert(
        [
            ContractRecord("NFO", "SBIN26SEP", "101", date(2026, 9, 24), "STOCK_FUTURE", "SBIN", 750),
            ContractRecord("NFO", "SBIN26OCT", "102", date(2026, 10, 29), "STOCK_FUTURE", "SBIN", 750),
            ContractRecord("NFO", "NIFTY26SEP", "999", date(2026, 9, 24), "INDEX_FUTURE", "NIFTY", 75),
        ]
    )
    return catalog


def test_both_selects_current_and_near_from_historical_catalog():
    result = select_cash_future(
        _catalog(),
        spot_instrument="NSE:3045:SBIN",
        underlying="SBIN",
        replay_date=date(2026, 9, 1),
        mode="BOTH",
    )
    assert [item.mode for item in result] == ["CURRENT", "NEAR"]
    assert [item.future.token for item in result] == ["101", "102"]


def test_index_future_is_not_selected_for_stock_cash_future():
    result = select_cash_future(
        _catalog(),
        spot_instrument="NSE:3045:SBIN",
        underlying="SBIN",
        replay_date=date(2026, 9, 1),
        mode="CURRENT",
    )
    assert result[0].future.instrument_type == "STOCK_FUTURE"
