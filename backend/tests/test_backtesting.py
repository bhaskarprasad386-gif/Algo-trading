import pytest

from app.algo.strategy import Strategy, StrategyRule, threshold_rule
from app.backtesting.engine import BacktestConfig, BacktestEngine


def test_backtest_enters_and_exits_on_strategy_rules():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    candles = [
        {"timestamp": 1, "close": 100, "signal": 1},
        {"timestamp": 2, "close": 110, "signal": 0},
    ]

    result = BacktestEngine().run(candles, entry, exit_)

    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 100
    assert result.trades[0].exit_price == 110
    assert result.net_pnl == 10
    assert result.win_rate == 1.0
    assert result.expectancy == 10
    assert result.sharpe_ratio == 0.0
    assert result.sortino_ratio == 0.0
    assert result.max_drawdown == 0.0


def test_backtest_applies_slippage_and_transaction_costs():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))
    config = BacktestConfig(
        initial_capital=1_000,
        quantity=2,
        slippage_rate=0.01,
        transaction_cost_rate=0.001,
    )

    result = BacktestEngine(config).run(
        [
            {"timestamp": 1, "close": 100, "signal": 1},
            {"timestamp": 2, "close": 110, "signal": 0},
        ],
        entry,
        exit_,
    )

    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(101)
    assert trade.exit_price == pytest.approx(108.9)
    assert trade.gross_pnl == pytest.approx(15.8)
    assert trade.costs == pytest.approx(0.4198)
    assert trade.net_pnl == pytest.approx(15.3802)
    assert result.expectancy == pytest.approx(15.3802)
    assert result.sharpe_ratio == 0.0
    assert result.sortino_ratio == 0.0
    assert result.max_drawdown == 0.0


def test_backtest_sharpe_ratio_uses_completed_trade_returns():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))

    result = BacktestEngine().run(
        [
            {"timestamp": 1, "close": 100, "signal": 1},
            {"timestamp": 2, "close": 110, "signal": 0},
            {"timestamp": 3, "close": 100, "signal": 1},
            {"timestamp": 4, "close": 105, "signal": 0},
        ],
        entry,
        exit_,
    )

    assert len(result.trades) == 2
    assert result.sharpe_ratio == pytest.approx(3.0)


def test_backtest_sortino_ratio_uses_only_downside_deviation():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))

    result = BacktestEngine().run(
        [
            {"timestamp": 1, "close": 100, "signal": 1},
            {"timestamp": 2, "close": 110, "signal": 0},
            {"timestamp": 3, "close": 100, "signal": 1},
            {"timestamp": 4, "close": 95, "signal": 0},
        ],
        entry,
        exit_,
    )

    assert len(result.trades) == 2
    assert result.sortino_ratio == pytest.approx(0.5)


def test_backtest_tracks_realized_max_drawdown():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))

    result = BacktestEngine().run(
        [
            {"timestamp": 1, "close": 100, "signal": 1},
            {"timestamp": 2, "close": 110, "signal": 0},
            {"timestamp": 3, "close": 110, "signal": 1},
            {"timestamp": 4, "close": 90, "signal": 0},
        ],
        entry,
        exit_,
    )

    assert len(result.trades) == 2
    assert result.net_pnl == -10
    assert result.expectancy == pytest.approx(-5)
    assert result.max_drawdown == pytest.approx(20 / 100_010)


def test_backtest_without_completed_trade_has_zero_pnl():
    entry = Strategy("entry", (StrategyRule("go", threshold_rule("signal", minimum=1)),))
    exit_ = Strategy("exit", (StrategyRule("stop", threshold_rule("signal", maximum=0)),))

    result = BacktestEngine().run(
        [{"timestamp": 1, "close": 100, "signal": 1}], entry, exit_
    )

    assert result.trades == ()
    assert result.net_pnl == 0
    assert result.win_rate == 0
    assert result.expectancy == 0
    assert result.sharpe_ratio == 0
    assert result.sortino_ratio == 0
    assert result.max_drawdown == 0
