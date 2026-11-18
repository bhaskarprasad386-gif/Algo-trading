import sqlite3

from app.backtesting.atomic_replay import AtomicReplayStore
from app.backtesting.high_resolution_ledger import HighResolutionLedgerWriter
from app.backtesting.ledger import BacktestLedger
from app.backtesting.resumable_high_resolution import ResumableHighResolutionRunner
from app.backtesting.universal import MarketEvent


class BuyThenSell:
    def __init__(self):
        self.seen = 0

    def on_event(self, event):
        self.seen += 1
        if self.seen == 1:
            return {"side": "BUY", "quantity": 1}
        if self.seen == 2:
            return {"side": "SELL", "quantity": 1}
        return None

    def snapshot_state(self):
        return {"seen": self.seen}

    def restore_state(self, state):
        self.seen = int(state["seen"])


def test_restart_restores_position_and_strategy_state(tmp_path):
    db = tmp_path / "restart.db"
    conn = sqlite3.connect(db)
    runner = ResumableHighResolutionRunner(conn, "run-1")
    first = runner.run([MarketEvent(10, "NIFTY", data=(("price", 100.0),))], BuyThenSell())
    assert first.events_processed == 1
    assert first.open_positions == 1
    conn.close()

    conn2 = sqlite3.connect(db)
    ledger = BacktestLedger(str(tmp_path / "trades.db"))
    ledger.start_run("run-1", "strategy", "1", 1000)
    writer = HighResolutionLedgerWriter(ledger, "run-1")
    resumed = ResumableHighResolutionRunner(conn2, "run-1")
    strategy = BuyThenSell()
    result = resumed.run([
        MarketEvent(10, "NIFTY", data=(("price", 100.0),)),
        MarketEvent(20, "NIFTY", data=(("price", 103.0),)),
    ], strategy, writer)

    assert result.events_processed == 2
    assert result.trades_closed == 1
    assert result.net_pnl == 3.0
    assert result.open_positions == 0
    assert result.resumed_from == (10, 0)
    assert strategy.seen == 2
    assert len(writer.records()) == 1
    assert writer.records()[0].payload["entry_timestamp_ns"] == 10
    assert writer.records()[0].payload["exit_timestamp_ns"] == 20
    atomic = AtomicReplayStore(conn2)
    assert len(atomic.trades("run-1")) == 1
    conn2.close()
    ledger.close()


def test_duplicate_source_event_is_not_delivered_twice(tmp_path):
    conn = sqlite3.connect(tmp_path / "dedup.db")
    runner = ResumableHighResolutionRunner(conn, "run-2")

    class Counter:
        def __init__(self):
            self.count = 0
        def on_event(self, event):
            self.count += 1
            return None

    strategy = Counter()
    result = runner.run([
        MarketEvent(1_000_000, "NIFTY", 7),
        MarketEvent(1_000_000, "NIFTY", 7),
        MarketEvent(1_000_001, "NIFTY", 8),
    ], strategy)
    assert result.events_processed == 2
    assert strategy.count == 2
    conn.close()


def test_failed_event_rolls_back_dedup_trade_and_checkpoint(tmp_path):
    db = tmp_path / "atomic.db"
    conn = sqlite3.connect(db)

    class FailsOnce:
        def on_event(self, event):
            raise RuntimeError("simulated crash")

    runner = ResumableHighResolutionRunner(conn, "run-3")
    try:
        runner.run([MarketEvent(100, "NIFTY", data=(("price", 100.0),))], FailsOnce())
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected simulated failure")

    store = AtomicReplayStore(conn)
    assert store.load_checkpoint("run-3") is None
    assert store.trades("run-3") == []
    assert conn.execute(
        "SELECT COUNT(*) FROM atomic_replay_events WHERE run_id='run-3'"
    ).fetchone()[0] == 0

    class Counter:
        def __init__(self): self.count = 0
        def on_event(self, event):
            self.count += 1
            return None

    strategy = Counter()
    result = ResumableHighResolutionRunner(conn, "run-3").run(
        [MarketEvent(100, "NIFTY", data=(("price", 100.0),))], strategy
    )
    assert result.events_processed == 1
    assert strategy.count == 1
    assert store.load_checkpoint("run-3") is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM atomic_replay_events WHERE run_id='run-3'"
    ).fetchone()[0] == 1
    conn.close()
