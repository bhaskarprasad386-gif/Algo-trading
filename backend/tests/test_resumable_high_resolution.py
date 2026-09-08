import sqlite3

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


def test_restart_resumes_after_checkpoint_without_replaying_events(tmp_path):
    db = tmp_path / "restart.db"
    conn = sqlite3.connect(db)
    runner = ResumableHighResolutionRunner(conn, "run-1")
    strategy = BuyThenSell()

    # First process only the first source event (simulated interruption).
    first = runner.run([MarketEvent(10, "NIFTY", data=(("price", 100.0),))], strategy)
    assert first.events_processed == 1
    assert first.open_positions == 1
    conn.close()

    # New process: replay the source from the beginning and resume at event 20.
    conn2 = sqlite3.connect(db)
    ledger = BacktestLedger(str(tmp_path / "trades.db"))
    ledger.start_run("run-1", "strategy", "1", 1000)
    writer = HighResolutionLedgerWriter(ledger, "run-1")
    resumed = ResumableHighResolutionRunner(conn2, "run-1")
    result = resumed.run([
        MarketEvent(10, "NIFTY", data=(("price", 100.0),)),
        MarketEvent(20, "NIFTY", data=(("price", 103.0),)),
    ], BuyThenSell(), writer)

    assert result.events_processed == 2
    assert result.trades_closed == 1
    assert result.net_pnl == 3.0
    assert result.resumed_from == (10, 0)
    assert len(writer.records()) == 1
    assert writer.records()[0].payload["entry_timestamp_ns"] == 10
    assert writer.records()[0].payload["exit_timestamp_ns"] == 20
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
