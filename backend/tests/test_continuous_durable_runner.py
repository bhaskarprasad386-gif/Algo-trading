from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.continuous_durable_runner import save_continuous_backtest_run
from app.backtesting.engine import BacktestResult


def test_continuous_durable_runner_saves_summary(tmp_path):
    result = BacktestResult(
        initial_capital=1000.0,
        final_capital=1080.0,
        net_pnl=80.0,
        total_return=0.08,
        win_rate=1.0,
        expectancy=80.0,
        sharpe_ratio=1.2,
        sortino_ratio=1.4,
        max_drawdown=0.0,
        cagr=0.08,
        trades=(),
    )

    ledger = BacktestTradeLedger(tmp_path / "ledger.sqlite")
    save_continuous_backtest_run(ledger, "continuous-run-1", result)

    summary = ledger.run_summary("continuous-run-1")
    assert summary is not None
    assert summary["net_pnl"] == 80.0
    assert summary["final_capital"] == 1080.0
    assert ledger.count("continuous-run-1") == 0
    ledger.close()
