from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.historical_arbitrage_runner import (
    ExitExecution,
    HistoricalArbitrageRunner,
    OpenPosition,
)
from app.backtesting.result_ledger import BacktestResultLedger


def _writer() -> tuple[BacktestResultLedger, BacktestRunWriter]:
    ledger = BacktestResultLedger()
    spec = BacktestRunSpec(
        run_id="arb-run",
        strategy_id="calendar-spread",
        strategy_version="v1",
        instrument="NFO:ABC",
        start_ns=1,
        end_ns=4,
        resolution=BacktestResolution("s", "historical", 1, 4),
    )
    return ledger, BacktestRunWriter(ledger, spec)


def test_entry_and_later_real_exit_are_persisted_incrementally() -> None:
    ledger, writer = _writer()
    runner = HistoricalArbitrageRunner(writer)

    def entry(event):
        if event["timestamp_ns"] == 1:
            return [OpenPosition(
                trade_id="cal-1", timestamp_ns=1, instrument="NFO:ABC",
                side="LONG_NEAR_SHORT_FAR", quantity=1, entry_price=100,
                contract="NEAR:2026-09-24|FAR:2026-10-29",
                expiry="2026-09-24/2026-10-29", strike=25000,
                leg="NEAR_FAR", data_resolution="s",
            )]
        return []

    def exit(position, event):
        if event["timestamp_ns"] == 3:
            return ExitExecution(3, 108, 8, fees=1, slippage=1, metadata={"exit_source": "real_quote"})
        return None

    assert runner.replay(
        [{"timestamp_ns": 1}, {"timestamp_ns": 2}, {"timestamp_ns": 3}, {"timestamp_ns": 4}], entry, exit
    ) == 1
    trade = ledger.trades("arb-run")[0]
    assert trade["timestamp_ns"] == 3
    assert trade["entry_price"] == 100
    assert trade["exit_price"] == 108
    assert trade["net_pnl"] == 6
    assert trade["metadata_json"]
    assert ledger.run("arb-run")["status"] == "COMPLETED"
    ledger.close()


def test_negative_gross_pnl_is_persisted_and_realized() -> None:
    ledger, writer = _writer()
    runner = HistoricalArbitrageRunner(writer)

    def entry(event):
        return [OpenPosition("loss-1", 1, "NFO:ABC", "LONG", 1, 50)] if event["timestamp_ns"] == 1 else []

    def exit(position, event):
        return ExitExecution(2, 45, -5, fees=1, slippage=0.5) if event["timestamp_ns"] == 2 else None

    assert runner.replay([{"timestamp_ns": 1}, {"timestamp_ns": 2}], entry, exit) == 1
    trade = ledger.trades("arb-run")[0]
    assert trade["gross_pnl"] == -5
    assert trade["net_pnl"] == -6.5
    assert runner.realized_pnl == -6.5
    ledger.close()


def test_missing_exit_is_audited_and_never_fabricated() -> None:
    ledger, writer = _writer()
    runner = HistoricalArbitrageRunner(writer)

    def entry(event):
        return [OpenPosition(
            trade_id="box-1", timestamp_ns=1, instrument="NFO:ABC", side="LONG", quantity=1, entry_price=50,
            contract="ABC-FUT", expiry="2026-09-24", strike=25000, leg="BOX", data_resolution="s",
        )] if event["timestamp_ns"] == 1 else []

    assert runner.replay([{"timestamp_ns": 1}, {"timestamp_ns": 2}], entry, lambda p, e: None) == 0
    assert ledger.trades("arb-run") == []
    events = ledger.events("arb-run")
    assert any(row["event_type"] == "UNRESOLVED_POSITION" for row in events)
    assert ledger.run("arb-run")["status"] == "COMPLETED"
    ledger.close()


def test_multiple_unresolved_positions_get_distinct_audit_sequences() -> None:
    ledger, writer = _writer()
    runner = HistoricalArbitrageRunner(writer)

    def entry(event):
        if event["timestamp_ns"] != 1:
            return []
        return [OpenPosition("p1", 1, "NFO:A", "LONG", 1, 10), OpenPosition("p2", 1, "NFO:B", "LONG", 1, 20)]

    runner.replay([{"timestamp_ns": 1}], entry, lambda p, e: None)
    events = ledger.events("arb-run")
    assert [row["sequence"] for row in events] == [1, 2, 3, 4]
    assert [row["event_type"] for row in events][-2:] == ["UNRESOLVED_POSITION", "UNRESOLVED_POSITION"]
    ledger.close()
