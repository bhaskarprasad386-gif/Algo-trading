from datetime import date, datetime, timezone

import pytest

from app.algo.strategy import Strategy, StrategyRule, threshold_rule
from app.backtesting.engine import BacktestConfig, BacktestEngine


def test_backtest_single_winning_trade():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    result = BacktestEngine(BacktestConfig(initial_capital=100_000, quantity=10)).run(
        [
            {"timestamp": 1, "close": 100, "signal": 1},
            {"timestamp": 2, "close": 110, "signal": 0},
        ],
        entry,
        exit_,
    )
    assert result.final_capital == 100_100
    assert result.net_pnl == 100
    assert result.total_return == pytest.approx(0.001)
    assert result.win_rate == 1.0
    assert result.expectancy == 100
    assert result.sharpe_ratio == 0.0
    assert result.sortino_ratio == 0.0
    assert result.max_drawdown == 0.0
    assert result.cagr == 0.0


def test_backtest_slippage_and_transaction_costs():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    config = BacktestConfig(
        initial_capital=100_000,
        quantity=10,
        slippage_bps=10,
        transaction_cost_bps=5,
    )
    result = BacktestEngine(config).run(
        [
            {"timestamp": 1, "close": 100, "signal": 1},
            {"timestamp": 2, "close": 102, "signal": 0},
        ],
        entry,
        exit_,
    )
    assert result.net_pnl == pytest.approx(15.3802)
    assert result.total_return == pytest.approx(0.000153802)
    assert result.expectancy == pytest.approx(15.3802)
    assert result.sharpe_ratio == 0.0
    assert result.sortino_ratio == 0.0
    assert result.cagr == 0.0


def test_backtest_expectancy_averages_completed_trade_pnl():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    result = BacktestEngine(BacktestConfig(initial_capital=100_000, quantity=10)).run(
        [
            {"timestamp": 1, "close": 100, "signal": 1},
            {"timestamp": 2, "close": 110, "signal": 0},
            {"timestamp": 3, "close": 110, "signal": 1},
            {"timestamp": 4, "close": 109, "signal": 0},
        ],
        entry,
        exit_,
    )
    assert result.expectancy == pytest.approx(45.0)
    assert result.sharpe_ratio == pytest.approx(-0.070710678, rel=1e-6)
    assert result.sortino_ratio == 0.0
    assert result.cagr == 0.0


def test_backtest_no_completed_trade_returns_zero_metrics():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    result = BacktestEngine(BacktestConfig(initial_capital=100_000, quantity=10)).run(
        [{"timestamp": 1, "close": 100, "signal": 1}], entry, exit_)
    assert result.expectancy == 0.0
    assert result.sharpe_ratio == 0.0
    assert result.sortino_ratio == 0.0
    assert result.cagr == 0.0


def test_backtest_max_drawdown_uses_realized_capital_path():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    config = BacktestConfig(initial_capital=100_000, quantity=10)
    result = BacktestEngine(config).run(
        [
            {"timestamp": 1, "close": 100, "signal": 1},
            {"timestamp": 2, "close": 110, "signal": 0},
            {"timestamp": 3, "close": 110, "signal": 1},
            {"timestamp": 4, "close": 108, "signal": 0},
        ],
        entry,
        exit_,
    )
    assert result.max_drawdown == pytest.approx(20 / 100_010)


def test_backtest_sharpe_uses_completed_trade_returns():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    config = BacktestConfig(initial_capital=100_000, quantity=10)
    result = BacktestEngine(config).run(
        [
            {"timestamp": 1, "close": 100, "signal": 1},
            {"timestamp": 2, "close": 110, "signal": 0},
            {"timestamp": 3, "close": 110, "signal": 1},
            {"timestamp": 4, "close": 105, "signal": 0},
        ],
        entry,
        exit_,
    )
    assert result.sharpe_ratio == pytest.approx(0.5 / (2 ** 0.5), rel=1e-6)


def test_backtest_sortino_uses_downside_trade_returns():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    config = BacktestConfig(initial_capital=100_000, quantity=10)
    result = BacktestEngine(config).run(
        [
            {"timestamp": 1, "close": 100, "signal": 1},
            {"timestamp": 2, "close": 110, "signal": 0},
            {"timestamp": 3, "close": 110, "signal": 1},
            {"timestamp": 4, "close": 105, "signal": 0},
        ],
        entry,
        exit_,
    )
    assert result.sortino_ratio == pytest.approx(2 ** 0.5 / 2, rel=1e-6)


def test_backtest_cagr_uses_completed_trade_duration():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 1, tzinfo=timezone.utc)

    config = BacktestConfig(initial_capital=1_000, quantity=10)
    result = BacktestEngine(config).run(
        [
            {"timestamp": start, "close": 100, "signal": 1},
            {"timestamp": end, "close": 110, "signal": 0},
        ],
        entry,
        exit_,
    )

    assert result.cagr == pytest.approx(0.1, rel=3e-3)
