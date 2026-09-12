from datetime import date, datetime, timezone

from app.backtesting.cash_future_strategy_runner import (
    CashFutureStrategyConfig,
    run_cash_future_strategy,
)
from app.scanner.cash_future_backtest import BacktestConfig, run_backtest
from app.scanner.cash_future_history import CashFutureHistoryPoint


def _point(day: int, gap: float, *, expiry_day: int = 29) -> CashFutureHistoryPoint:
    timestamp = datetime(2026, 1, day, 10, 0, tzinfo=timezone.utc)
    return CashFutureHistoryPoint(
        timestamp=timestamp,
        symbol="TEST",
        contract_month="2026-01",
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=500,
        margin_required=50_000.0,
        expiry_date=date(2026, 1, expiry_day),
    )


def test_legacy_backtest_does_not_open_new_position_on_expiry_day():
    result = run_backtest(
        [_point(29, 8.0)],
        BacktestConfig(min_entry_gap=5.0),
    )

    assert result["trade_count"] == 0
    assert result["open_position"] is None
    assert result["net_profit"] == 0.0


def test_legacy_backtest_allows_pre_expiry_entry_and_exits_on_expiry():
    result = run_backtest(
        [_point(28, 8.0), _point(29, 3.0)],
        BacktestConfig(min_entry_gap=5.0, exit_gap=0.0),
    )

    assert result["trade_count"] == 1
    assert result["trades"][0]["entry_time"] == _point(28, 8.0).timestamp.isoformat()
    assert result["trades"][0]["exit_reason"] == "expiry"
    assert result["open_position"] is None


def test_strategy_runner_blocks_new_expiry_day_buy_and_records_reason():
    result = run_cash_future_strategy(
        [_point(29, 8.0)],
        lambda point, history: "BUY",
        strategy_id="expiry-gate-test",
        config=CashFutureStrategyConfig(contract_month="2026-01"),
    )

    assert result.trades == ()
    assert result.blocked_entry_count == 0
    assert len(result.signals) == 1
    assert result.signals[0]["execution_status"] == "blocked"
    assert result.signals[0]["blocked_reason"] == "expiry_day_new_entry"


def test_strategy_runner_keeps_pre_expiry_entry_until_expiry_exit():
    result = run_cash_future_strategy(
        [_point(28, 8.0), _point(29, 3.0)],
        lambda point, history: "BUY",
        strategy_id="expiry-exit-test",
        config=CashFutureStrategyConfig(contract_month="2026-01"),
    )

    assert len(result.trades) == 1
    assert result.trades[0]["exit_reason"] == "expiry"
    assert result.trades[0]["entry_time"] == _point(28, 8.0).timestamp.isoformat()
    assert result.final_reserved_margin == 0.0
