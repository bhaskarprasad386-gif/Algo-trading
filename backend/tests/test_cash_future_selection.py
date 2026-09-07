from datetime import date

import pytest

from app.backtesting.cash_future_selection import select_cash_future
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord


def _catalog():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(
        date(2026, 8, 1),
        [ContractRecord("NFO", "SBIN26AUG", "old", date(2026, 8, 27), "STOCK_FUTURE", "SBIN", 750),
         ContractRecord("NFO", "SBIN26SEP", "old2", date(2026, 9, 24), "STOCK_FUTURE", "SBIN", 750)],
    )
    catalog.upsert_snapshot(
        date(2026, 9, 1),
        [ContractRecord("NFO", "SBIN26SEP", "101", date(2026, 9, 24), "STOCK_FUTURE", "SBIN", 750),
         ContractRecord("NFO", "SBIN26OCT", "102", date(2026, 10, 29), "STOCK_FUTURE", "SBIN", 750),
         ContractRecord("NFO", "NIFTY26SEP", "999", date(2026, 9, 24), "INDEX_FUTURE", "NIFTY", 75)],
    )
    return catalog


def test_both_selects_current_and_near_from_snapshot_at_replay_date():
    result = select_cash_future(_catalog(), spot_instrument="NSE:3045:SBIN", underlying="SBIN", replay_date=date(2026, 9, 1), mode="BOTH")
    assert [item.mode for item in result] == ["CURRENT", "NEAR"]
    assert [item.future.token for item in result] == ["101", "102"]


def test_old_replay_date_does_not_substitute_new_snapshot_contract():
    result = select_cash_future(_catalog(), spot_instrument="NSE:3045:SBIN", underlying="SBIN", replay_date=date(2026, 8, 15), mode="CURRENT")
    assert result[0].future.token == "old"
    assert result[0].future.snapshot_date == date(2026, 8, 1)


def test_future_index_is_not_selected_for_stock_strategy():
    result = select_cash_future(_catalog(), spot_instrument="NSE:3045:SBIN", underlying="SBIN", replay_date=date(2026, 9, 1), mode="CURRENT")
    assert result[0].future.instrument_type == "STOCK_FUTURE"


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        select_cash_future(_catalog(), spot_instrument="NSE:3045:SBIN", underlying="SBIN", replay_date=date(2026, 9, 1), mode="ROLL")
