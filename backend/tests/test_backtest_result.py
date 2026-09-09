from __future__ import annotations

from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.result_ledger import BacktestResultLedger, EquityPoint
from app.execution.payoff import PayoffLeg


def make_spec() -> BacktestRunSpec:
    return BacktestRunSpec(
        run_id="run-payoff",
        strategy_id="calendar_spread",
        strategy_version="1",
        instrument="NIFTY",
        start_ns=100,
        end_ns=200,
        resolution=BacktestResolution("s", "historical", 0, 500),
        parameters={"direction": "LONG_NEAR_SHORT_FAR"},
        data_watermarks={"NIFTY": 500},
    )


def test_writer_persists_payoff_and_equity_incrementally() -> None:
    ledger = BacktestResultLedger()
    writer = BacktestRunWriter(ledger, make_spec(), created_at_ns=100)

    legs = (
        PayoffLeg("CALL", "BUY", 100.0, 5.0, 1),
        PayoffLeg("CALL", "SELL", 110.0, 2.0, 1),
    )
    snapshot = writer.record_payoff(1, 150, legs, (90.0, 100.0, 110.0, 120.0))
    writer.record_equity(EquityPoint(150, 4.0, 4.0, 0.0, 0.0))
    writer.complete()

    assert snapshot.run_id == "run-payoff"
    assert len(snapshot.prices) == len(snapshot.pnl) == 4
    assert 103.0 in snapshot.break_even_points
    assert ledger.run("run-payoff")["status"] == "COMPLETED"
    assert ledger.events("run-payoff")[0]["event_type"] == "PAYOFF_SNAPSHOT"
    assert ledger.equity("run-payoff")[0]["equity"] == 4.0


def test_failed_run_records_auditable_reason() -> None:
    ledger = BacktestResultLedger()
    writer = BacktestRunWriter(ledger, make_spec())
    writer.fail("missing genuine historical coverage")

    assert ledger.run("run-payoff")["status"] == "FAILED"
    events = ledger.events("run-payoff")
    assert events[-1]["event_type"] == "RUN_FAILED"
