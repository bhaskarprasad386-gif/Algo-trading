from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.historical_arbitrage_service import HistoricalArbitrageBacktestService
from app.backtesting.historical_arbitrage_runner import ExitExecution, OpenPosition
from app.backtesting.result_ledger import BacktestResultLedger
from app.execution.payoff import PayoffLeg


def _writer(tmp_path):
    ledger = BacktestResultLedger(tmp_path / "results.db")
    spec = BacktestRunSpec(
        run_id="unified-1", strategy_id="calendar", strategy_version="v1",
        instrument="NFO:ABC", start_ns=1, end_ns=3,
        resolution=BacktestResolution("s", "test", 1, 3), parameters={"expiry": "2026-09-24"},
    )
    return ledger, BacktestRunWriter(ledger, spec)


def test_service_persists_trade_and_payoff_without_fabricating_exit(tmp_path):
    ledger, writer = _writer(tmp_path)
    service = HistoricalArbitrageBacktestService(writer)

    def entry(event):
        if event["timestamp_ns"] == 1:
            return (OpenPosition("t1", 1, "NFO:ABC", "LONG", 1, 2.0,
                                 contract="CALENDAR:20260924:20261029", expiry="20260924",
                                 leg="CALENDAR", data_resolution="1s"),)
        return ()

    def exit(position, event):
        if event["timestamp_ns"] == 3:
            return ExitExecution(3, 5.0, 3.0, fees=0.5, slippage=0.25)
        return None

    result = service.run(
        ({"timestamp_ns": 1}, {"timestamp_ns": 2}, {"timestamp_ns": 3}),
        entry_selector=entry, exit_selector=exit,
        payoff_legs=(PayoffLeg("FUTURE", "BUY", None, 100.0, 1),),
        payoff_prices=(99.0, 100.0, 101.0),
    )
    assert result.completed_trades == 1
    assert result.unresolved_trades == 0
    assert result.realized_pnl == 2.25
    assert result.payoff is not None
    assert len(ledger.trades("unified-1")) == 1
    assert len(ledger.events("unified-1")) == 3
    assert ledger.run("unified-1")["status"] == "COMPLETED"
