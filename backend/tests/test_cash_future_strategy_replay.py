from datetime import date

import pytest

from app.backtesting.cash_future_strategy_replay import HistoricalCashFutureReplay
from app.backtesting.cash_future_selection import select_cash_future
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.cash_future_replay import CashFutureBar


def _catalog():
    c = ContractMasterCatalog()
    c.upsert_snapshot(date(2026, 9, 1), [
        ContractRecord("NFO", "SBIN26SEP", "101", date(2026, 9, 24), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBIN26OCT", "102", date(2026, 10, 29), "STOCK_FUTURE", "SBIN", 750),
    ])
    return c


def _bars(token, symbol, spot0=100.0, future0=102.0):
    instrument = f"NFO:{token}:{symbol}"
    return instrument, [CashFutureBar(1, spot0, future0), CashFutureBar(2, spot0 + 5, future0 - 6)]


def test_both_resolves_two_locked_legs_and_replays_them():
    sep, sep_bars = _bars("101", "SBIN26SEP")
    oct_, oct_bars = _bars("102", "SBIN26OCT", 100, 103)
    runner = HistoricalCashFutureReplay(_catalog())
    plan = runner.run({sep: sep_bars, oct_: oct_bars}, spot_instrument="NSE:3045:SBIN", underlying="SBIN",
                      replay_date=date(2026, 9, 1), mode="BOTH", entry_timestamps=[1], exit_timestamps=[2], quantity=1)
    assert [x.future_instrument for x in plan.trades] == [sep, oct_]
    assert [x.lot_size for x in plan.trades] == [750, 750]
    assert [x.result.gross_pnl for x in plan.trades] == [8250, 9000]


def test_missing_selected_future_bars_fail_closed():
    runner = HistoricalCashFutureReplay(_catalog())
    with pytest.raises(LookupError):
        runner.run({}, spot_instrument="NSE:3045:SBIN", underlying="SBIN", replay_date=date(2026, 9, 1),
                   mode="CURRENT", entry_timestamps=[1], exit_timestamps=[2])
