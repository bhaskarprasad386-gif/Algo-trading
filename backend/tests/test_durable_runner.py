from app.algo.strategy import Strategy, StrategyRule
from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.durable_runner import run_incremental_to_durable_ledger
from app.backtesting.engine import BacktestConfig, BacktestEngine


def _close_is(value: float) -> Strategy:
    return Strategy(
        name=str(value),
        rules=(StrategyRule("close", lambda context: context.get("close") == value),),
    )


def test_durable_runner_saves_completed_run_summary(tmp_path):
    ledger = BacktestTradeLedger(tmp_path / "ledger.sqlite")
    engine = BacktestEngine(BacktestConfig(initial_capital=1000.0))
    candles = (
        {"timestamp": 1, "close": 100.0},
        {"timestamp": 2, "close": 130.0},
    )

    result = run_incremental_to_durable_ledger(
        engine,
        candles,
        _close_is(100.0),
        _close_is(130.0),
        ledger=ledger,
        run_id="durable-run-1",
        chunk_size=1,
    )

    summary = ledger.run_summary("durable-run-1")
    assert summary is not None
    assert summary["initial_capital"] == result.initial_capital
    assert summary["final_capital"] == result.final_capital
    assert summary["net_pnl"] == result.net_pnl
    assert summary["total_return"] == result.total_return
    assert ledger.count("durable-run-1") == 1
    assert ledger.net_pnl("durable-run-1") == result.net_pnl
    ledger.close()
