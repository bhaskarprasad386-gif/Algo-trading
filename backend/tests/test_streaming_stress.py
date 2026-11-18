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


def test_historical_catalog_bounded_batch_is_atomic_and_deduplicated(tmp_path):
    from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord

    catalog = HistoricalCatalog(str(tmp_path / "batch.sqlite3"))
    records = [
        HistoricalRecord("angelone-live-1s", "ABC|1", "1s", i * 1_000_000_000, {"ltp": i})
        for i in range(100)
    ]
    assert catalog.ingest_if_absent_batch(records) == 100
    assert catalog.ingest_if_absent_batch(records) == 0
    assert catalog.count(source="angelone-live-1s", instrument="ABC|1", timeframe="1s") == 100
    assert catalog._db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert catalog._db.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert catalog._db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    catalog.close()


def test_latest_message_buffer_preserves_distinct_seconds_and_deduplicates():
    from app.market_data.live_cash_future_stream import _LatestMessageBuffer

    buffer = _LatestMessageBuffer()
    for timestamp in (1_000_000_000, 1_001_000_000_000, 1_001_000_000_000, 1_002_000_000_000):
        buffer.put({"token": "101", "exchange_timestamp": timestamp, "last_traded_price": "10000"})
    assert len(buffer) == 3
    drained = buffer.drain()
    assert [m["exchange_timestamp"] for m in drained] == [1_000_000_000, 1_001_000_000_000, 1_002_000_000_000]


def test_market_collector_retry_backoff_bounds():
    from app.market_data.live_cash_future_stream import NO_DATA_RETRY_INITIAL_SECONDS, NO_DATA_RETRY_MAX_SECONDS
    delay = NO_DATA_RETRY_INITIAL_SECONDS
    values = []
    for _ in range(20):
        values.append(delay)
        delay = min(NO_DATA_RETRY_MAX_SECONDS, delay * 2.0)
    assert values[:4] == [10.0, 20.0, 40.0, 60.0]
    assert max(values) == 60.0


def test_backtest_worker_cleanup_removes_completed_future():
    from concurrent.futures import Future
    from app.scanner import backtest_jobs

    future = Future()
    future.set_result(None)
    with backtest_jobs._LOCK:
        backtest_jobs._FUTURES["finished-test"] = future
    assert backtest_jobs.cleanup_finished_workers() == 1
    with backtest_jobs._LOCK:
        assert "finished-test" not in backtest_jobs._FUTURES
