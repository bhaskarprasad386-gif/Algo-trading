from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.historical_strategy_runner import Execution, HistoricalStrategyRunner
from app.backtesting.result_ledger import BacktestResultLedger


def test_replay_persists_trade():
    ledger = BacktestResultLedger()
    spec = BacktestRunSpec("run-1", "box", "1", "NIFTY", 1, 10, BacktestResolution("s", "historical", 1, 10), {}, {})
    runner = HistoricalStrategyRunner(BacktestRunWriter(ledger, spec))
    def strategy(event):
        if event["timestamp_ns"] == 2:
            return Execution("t1", 2, "NIFTY", "BUY", 50, 10, 12, 100, fees=5)
        return None
    assert runner.replay(({"timestamp_ns": 1}, {"timestamp_ns": 2}), strategy) == 1
    assert ledger.trades("run-1")[0]["net_pnl"] == 95
    assert ledger.run("run-1")["status"] == "COMPLETED"


def test_failure_is_audited():
    ledger = BacktestResultLedger()
    spec = BacktestRunSpec("run-2", "calendar", "1", "NIFTY", 1, 10, BacktestResolution("s", "historical", 1, 10), {}, {})
    runner = HistoricalStrategyRunner(BacktestRunWriter(ledger, spec))
    def broken(_):
        raise RuntimeError("missing exit quote")
    try:
        runner.replay(({"timestamp_ns": 2},), broken)
    except RuntimeError:
        pass
    assert ledger.run("run-2")["status"] == "FAILED"
    assert ledger.events("run-2")[-1]["event_type"] == "RUN_FAILED"
