import sqlite3

from app.backtesting.resumable_high_resolution_v2 import RestartableHighResolutionRunner
from app.backtesting.universal import MarketEvent


class StatefulCounter:
    def __init__(self):
        self.seen = 0

    def on_event(self, event):
        self.seen += 1
        return None

    def snapshot_state(self):
        return {"seen": self.seen}

    def restore_state(self, state):
        self.seen = int(state["seen"])


def test_restart_restores_strategy_state_and_skips_checkpointed_events(tmp_path):
    db = tmp_path / "resume.db"
    conn = sqlite3.connect(db)
    first = RestartableHighResolutionRunner(conn, "run-1")
    strategy1 = StatefulCounter()
    result1 = first.run([MarketEvent(10, "NIFTY", 1)], strategy1)
    assert result1.events_processed == 1
    assert strategy1.seen == 1
    conn.close()

    conn2 = sqlite3.connect(db)
    second = RestartableHighResolutionRunner(conn2, "run-1")
    strategy2 = StatefulCounter()
    result2 = second.run([
        MarketEvent(10, "NIFTY", 1),
        MarketEvent(20, "NIFTY", 1),
    ], strategy2)
    assert result2.resumed_from == (10, 1)
    assert result2.events_processed == 2
    assert strategy2.seen == 2
    conn2.close()
