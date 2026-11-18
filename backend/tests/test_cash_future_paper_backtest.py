from datetime import date, datetime, timedelta

from app.scanner.cash_future_paper_backtest import PaperBacktestConfig, run_cash_future_paper_backtest
from app.scanner.synchronized_replay import ReplayBar


def test_cash_future_paper_backtest_uses_20_lakh_and_tracks_three_legs():
    start = datetime(2026, 1, 1, 10, 0)
    bars = [
        ReplayBar(start, 100.0, 105.0, 108.0, date(2026, 1, 1), date(2026, 1, 2), 10),
        ReplayBar(start + timedelta(minutes=1), 101.0, 104.0, 107.0, date(2026, 1, 1), date(2026, 1, 2), 10),
        ReplayBar(start + timedelta(minutes=2), 102.0, 103.0, 106.0, date(2026, 1, 1), date(2026, 1, 2), 10),
        ReplayBar(start + timedelta(days=1), 103.0, 103.0, 103.0, date(2026, 1, 1), date(2026, 1, 2), 10),
    ]

    result = run_cash_future_paper_backtest(
        bars,
        PaperBacktestConfig(starting_capital=2_000_000.0, min_entry_gap=5.0, exit_gap=0.0),
    )

    assert result["status"] == "completed"
    assert result["starting_capital"] == 2_000_000.0
    assert result["lot_size"] == 10
    assert result["entry_current_gap"] == 5.0
    assert result["entry_near_gap"] == 8.0
    assert result["ledger"]
    assert {"spot", "current_future", "near_future", "current_gap", "near_gap", "lot_size"} <= result["ledger"][0].keys()
