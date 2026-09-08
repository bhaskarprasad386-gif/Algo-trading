import sqlite3
import tracemalloc

from app.backtesting.atomic_replay import AtomicReplayStore
from app.backtesting.resumable_high_resolution import ResumableHighResolutionRunner
from app.backtesting.universal import MarketEvent


class BoundedCounter:
    def __init__(self):
        self.count = 0

    def on_event(self, event):
        self.count += 1
        return None


def event_source(count):
    for index in range(count):
        yield MarketEvent(index + 1, "NIFTY", index, data=(("price", 100.0),))


def test_large_stream_is_consumed_incrementally(tmp_path):
    count = 10_000
    conn = sqlite3.connect(tmp_path / "stream.db")
    strategy = BoundedCounter()
    runner = ResumableHighResolutionRunner(conn, "stress")

    tracemalloc.start()
    result = runner.run(event_source(count), strategy)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    store = AtomicReplayStore(conn)
    assert result.events_processed == count
    assert strategy.count == count
    assert store.count_events("stress") == count
    assert store.count_trades("stress") == 0
    # The replay layer must not retain O(n) Python event history.
    assert peak < 32 * 1024 * 1024
    conn.close()
