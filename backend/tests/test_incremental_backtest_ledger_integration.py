from app.algo.strategy import Strategy, StrategyRule
from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.engine import BacktestConfig, BacktestEngine


def _always(value: bool) -> Strategy:
    return Strategy(name=str(value), rules=(StrategyRule("signal", lambda context: value),))


def test_incremental_backtest_can_persist_trade_chunks_to_sqlite(tmp_path):
    ledger = BacktestTradeLedger(tmp_path / "trades.sqlite")
    engine = BacktestEngine(BacktestConfig(initial_capital=1000.0))
    candles = (
        {"timestamp": 1, "close": 100.0},
        {"timestamp": 2, "close": 110.0},
        {"timestamp": 3, "close": 120.0},
        {"timestamp": 4, "close": 130.0},
    )
    calls = []

    result = engine.run_incremental(
        candles,
        _always(True),
        _always(False),
        persist_chunk=lambda trades, sequence: calls.append((sequence, trades)) or ledger.append("run-1", sequence, trades),
        chunk_size=1,
    )

    assert result.trades == ()
    assert ledger.count("run-1") == 0
    assert calls == []
    ledger.close()
