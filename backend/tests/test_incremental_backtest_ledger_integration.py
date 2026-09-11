from app.algo.strategy import Strategy, StrategyRule
from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.engine import BacktestConfig, BacktestEngine


def _close_is(value: float) -> Strategy:
    return Strategy(
        name=str(value),
        rules=(StrategyRule("close", lambda context: context.get("close") == value),),
    )


def test_incremental_backtest_persists_trade_chunks_to_sqlite(tmp_path):
    ledger = BacktestTradeLedger(tmp_path / "trades.sqlite")
    engine = BacktestEngine(BacktestConfig(initial_capital=1000.0))
    candles = (
        {"timestamp": 1, "close": 100.0},
        {"timestamp": 2, "close": 110.0},
        {"timestamp": 3, "close": 120.0},
        {"timestamp": 4, "close": 130.0},
    )

    result = engine.run_incremental_to_ledger(
        candles,
        _close_is(100.0),
        _close_is(130.0),
        ledger=ledger,
        run_id="run-1",
        chunk_size=1,
    )

    assert result.trades == ()
    assert ledger.count("run-1") == 1
    assert ledger.net_pnl("run-1") == 30.0
    trade = ledger.trades("run-1")[0]
    assert trade.entry_price == 100.0
    assert trade.exit_price == 130.0
    ledger.close()
