from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.engine import BacktestEngine, EventSignal
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.resumable_runner import run_resumable_events


def _event(ts, price, seq):
    return HistoricalRecord(
        source="test", instrument="TEST", timeframe="tick",
        timestamp_ns=ts, payload={"price": price}, sequence=seq,
    )


def _strategy(context):
    price = float(context.payload["price"])
    if price == 100:
        return EventSignal("BUY")
    if price == 110:
        return EventSignal("SELL")
    return EventSignal("NONE")


def test_resumable_runner_resume_does_not_duplicate_checkpointed_events(tmp_path):
    ledger = BacktestTradeLedger(tmp_path / "resume.sqlite")
    engine = BacktestEngine()
    events = (_event(1, 100, 1), _event(2, 110, 2), _event(3, 120, 3))

    ledger.save_checkpoint("run-1", "2:2", 1)
    result = run_resumable_events(
        engine, events, _strategy, ledger=ledger, run_id="run-1", chunk_size=1,
    )

    assert ledger.count("run-1") == 0
    assert result.net_pnl == 0.0
    assert ledger.checkpoint("run-1")["cursor"] == "3:3"
    ledger.close()


def test_resumable_runner_persists_trade_and_checkpoint(tmp_path):
    ledger = BacktestTradeLedger(tmp_path / "fresh.sqlite")
    engine = BacktestEngine()
    events = (_event(1, 100, 1), _event(2, 110, 2))

    result = run_resumable_events(
        engine, events, _strategy, ledger=ledger, run_id="run-2", chunk_size=1,
    )

    assert ledger.count("run-2") == 1
    assert ledger.net_pnl("run-2") == result.net_pnl
    assert ledger.checkpoint("run-2")["cursor"] == "2:2"
    ledger.close()
