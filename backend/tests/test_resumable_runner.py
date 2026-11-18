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


def test_resumable_runner_preserves_position_across_chunk_boundary(tmp_path):
    ledger = BacktestTradeLedger(tmp_path / "chunk.sqlite")
    engine = BacktestEngine()
    events = (_event(1, 100, 1), _event(2, 110, 2))

    result = run_resumable_events(
        engine, events, _strategy, ledger=ledger, run_id="run-1", chunk_size=1,
    )

    assert ledger.count("run-1") == 1
    assert ledger.net_pnl("run-1") == 10.0
    assert result.net_pnl == 10.0
    assert result.trades[0].entry_price == 100.0
    assert result.trades[0].exit_price == 110.0
    assert ledger.checkpoint("run-1")["cursor"] == "2:2:test:TEST:tick"
    ledger.close()


def test_resumable_runner_accepts_one_pass_generator(tmp_path):
    ledger = BacktestTradeLedger(tmp_path / "generator.sqlite")

    def events():
        for ts, price, seq in ((1, 100, 1), (2, 110, 2)):
            yield _event(ts, price, seq)

    result = run_resumable_events(
        BacktestEngine(), events(), _strategy,
        ledger=ledger, run_id="generator-run", chunk_size=1,
    )

    assert ledger.count("generator-run") == 1
    assert result.net_pnl == 10.0
    assert ledger.checkpoint("generator-run")["cursor"] == "2:2:test:TEST:tick"
    ledger.close()


def test_resumable_runner_reconstructs_open_position_after_restart(tmp_path):
    ledger = BacktestTradeLedger(tmp_path / "restart.sqlite")
    events = (_event(1, 100, 1), _event(2, 110, 2))

    # Simulate an interruption immediately after the BUY event. No trade has
    # closed yet, but the checkpoint identifies the last processed event.
    ledger.save_checkpoint("run-2", "1:1:test:TEST:tick", 0)

    result = run_resumable_events(
        BacktestEngine(), events, _strategy,
        ledger=ledger, run_id="run-2", chunk_size=1,
    )

    assert ledger.count("run-2") == 1
    assert ledger.net_pnl("run-2") == 10.0
    assert result.net_pnl == 10.0
    assert ledger.checkpoint("run-2")["cursor"] == "2:2:test:TEST:tick"

    # A completed run is restart-safe and must not duplicate the trade.
    rerun = run_resumable_events(
        BacktestEngine(), events, _strategy, ledger=ledger, run_id="run-2", chunk_size=1,
    )
    assert ledger.count("run-2") == 1
    assert rerun.net_pnl == 10.0
    ledger.close()


def test_resumable_runner_rolls_back_trade_when_checkpoint_fails(tmp_path, monkeypatch):
    ledger = BacktestTradeLedger(tmp_path / "atomic.sqlite")
    events = (_event(1, 100, 1), _event(2, 110, 2))

    original_save = ledger.save_checkpoint
    calls = 0

    def fail_checkpoint(run_id, cursor, trade_count):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated checkpoint failure")
        return original_save(run_id, cursor, trade_count)

    monkeypatch.setattr(ledger, "save_checkpoint", fail_checkpoint)

    try:
        run_resumable_events(
            BacktestEngine(), events, _strategy,
            ledger=ledger, run_id="atomic-run", chunk_size=2,
        )
    except RuntimeError as exc:
        assert str(exc) == "simulated checkpoint failure"
    else:
        raise AssertionError("expected checkpoint failure")

    assert ledger.count("atomic-run") == 0
    assert ledger.checkpoint("atomic-run") is None

    monkeypatch.setattr(ledger, "save_checkpoint", original_save)
    result = run_resumable_events(
        BacktestEngine(), events, _strategy,
        ledger=ledger, run_id="atomic-run", chunk_size=2,
    )
    assert result.net_pnl == 10.0
    assert ledger.count("atomic-run") == 1
    assert ledger.checkpoint("atomic-run")["cursor"] == "2:2:test:TEST:tick"
    ledger.close()
