from datetime import datetime, timezone

import pytest

from app.algo.strategy import Strategy, StrategyRule
from app.backtesting.engine import BacktestConfig, BacktestEngine


def _strategy(name, field, expected):
    return Strategy(name, (StrategyRule(name, lambda context: context.get(field) == expected),))


def test_engine_is_strategy_agnostic_and_replays_custom_rules():
    entry = _strategy("enter-on-signal", "signal", 1.0)
    exit_ = _strategy("exit-on-signal", "exit", 1.0)
    candles = [
        {"timestamp": 1, "close": 100.0, "signal": 1.0, "exit": 0.0},
        {"timestamp": 2, "close": 110.0, "signal": 0.0, "exit": 1.0},
    ]

    result = BacktestEngine(BacktestConfig(initial_capital=10_000, quantity=2)).run(candles, entry, exit_)

    assert result.net_pnl == pytest.approx(20.0)
    assert len(result.trades) == 1
    assert result.trades[0].quantity == 2


def test_incremental_engine_persists_bounded_trade_chunks():
    entry = _strategy("entry", "entry", 1.0)
    exit_ = _strategy("exit", "exit", 1.0)
    candles = []
    for i in range(1, 9):
        candles.append({"timestamp": i, "close": 100.0 + i, "entry": 1.0 if i % 2 == 1 else 0.0, "exit": 1.0 if i % 2 == 0 else 0.0})

    persisted = []
    result = BacktestEngine(BacktestConfig(initial_capital=1_000)).run_incremental(
        candles, entry, exit_, persist_chunk=lambda trades, sequence: persisted.append((sequence, trades)), chunk_size=2
    )

    assert result.trades == ()
    assert [len(trades) for _, trades in persisted] == [2, 2]
    assert [sequence for sequence, _ in persisted] == [0, 1]
    assert result.net_pnl > 0


def test_timestamped_trades_produce_cagr():
    entry = _strategy("entry", "entry", 1.0)
    exit_ = _strategy("exit", "exit", 1.0)
    candles = [
        {"timestamp": datetime(2025, 1, 1, tzinfo=timezone.utc), "close": 100.0, "entry": 1.0, "exit": 0.0},
        {"timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc), "close": 110.0, "entry": 0.0, "exit": 1.0},
    ]
    result = BacktestEngine(BacktestConfig(initial_capital=1_000)).run(candles, entry, exit_)
    assert result.cagr > 0
