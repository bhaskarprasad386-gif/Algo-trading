from datetime import date, datetime, timedelta

from app.scanner.cash_future_backtest import BacktestConfig, run_backtest
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(ts, gap, expiry=date(2026, 9, 30), month="CURRENT"):
    return CashFutureHistoryPoint(
        timestamp=ts,
        symbol="ABC",
        contract_month=month,
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=100,
        margin_required=10000.0,
        expiry_date=expiry,
    )


def test_backtest_enters_on_gap_and_exits_on_convergence():
    now = datetime(2026, 9, 2, 10, 0)
    result = run_backtest(
        [point(now, 10.0), point(now + timedelta(hours=1), 4.0)],
        BacktestConfig(min_entry_gap=8.0, exit_gap=5.0, charges_per_trade=20.0, funding_cost_per_trade=10.0),
    )
    assert result["trade_count"] == 1
    assert result["wins"] == 1
    assert result["net_profit"] == 570.0
    assert result["trades"][0]["exit_reason"] == "convergence"


def test_backtest_exits_on_expiry_without_mixing_contracts():
    now = datetime(2026, 9, 29, 15, 0)
    result = run_backtest(
        [point(now, 8.0), point(datetime(2026, 9, 30, 15, 30), 3.0)],
        BacktestConfig(min_entry_gap=5.0, exit_gap=0.0),
    )
    assert result["trade_count"] == 1
    assert result["trades"][0]["exit_reason"] == "expiry"

    near_result = run_backtest(
        [point(now, 9.0, month="NEAR"), point(now + timedelta(hours=1), 2.0, month="NEAR")],
        BacktestConfig(min_entry_gap=5.0, exit_gap=3.0),
    )
    assert near_result["trade_count"] == 1
    assert near_result["trades"][0]["lot_size"] == 100
