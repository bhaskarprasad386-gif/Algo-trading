from app.backtesting.engine import BacktestConfig
from app.backtesting.strategy_runner import GenericStrategyRunner
from app.algo.strategy import Strategy, StrategyRule, threshold_rule


def test_generic_runner_executes_arbitrary_strategy_without_strategy_specific_engine():
    entry = Strategy("threshold-entry", (StrategyRule("close-min", threshold_rule("close", minimum=100)),))
    exit_ = Strategy("threshold-exit", (StrategyRule("close-max", threshold_rule("close", minimum=105)),))
    candles = ({"timestamp": i, "close": value} for i, value in enumerate((99, 100, 103, 105), 1))

    result = GenericStrategyRunner(BacktestConfig(initial_capital=10_000, quantity=2)).run(
        candles, entry=entry, exit=exit_
    )

    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 100
    assert result.trades[0].exit_price == 105
    assert result.net_pnl == 10


def test_generic_runner_supports_incremental_persistence():
    entry = Strategy("entry", (StrategyRule("close-min", threshold_rule("close", minimum=100)),))
    exit_ = Strategy("exit", (StrategyRule("close-max", threshold_rule("close", minimum=101)),))
    saved = []

    result = GenericStrategyRunner().run_incremental(
        ({"timestamp": i, "close": value} for i, value in enumerate((100, 101, 100, 102), 1)),
        entry=entry,
        exit=exit_,
        persist_chunk=lambda rows, sequence: saved.append((sequence, rows)),
        chunk_size=1,
    )

    assert result.trades == ()
    assert len(saved) == 2
    assert result.net_pnl == 3
