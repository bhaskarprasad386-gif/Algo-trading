from threading import Event
from time import time_ns

import pytest

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.contracts import InstrumentType, MarketDataRecord
from app.market_data.shared_index_local_feed import SharedIndexRunnerLocalFeedManager
from app.market_data.shared_index_market_data_state import SharedIndexMarketDataState


class FakeSharedRunner:
    def __init__(self, keys=()):
        self.state = SharedIndexMarketDataState()
        self.state.retain_instruments(keys)

    def publish(self, record):
        return self.state.on_record(record)


def _spot():
    feed = CommonStrategyMarketFeed("box-local-test", manager=object())
    descriptor = feed.descriptor(
        exchange="NSE",
        token="999",
        symbol="NIFTY",
        instrument_type="index",
        underlying="NIFTY",
    )
    record = MarketDataRecord(
        instrument=descriptor.key,
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=time_ns(),
        ltp=24000.0,
        underlying="NIFTY",
    )
    return descriptor, record


def test_local_feed_fans_out_only_shared_runner_records():
    descriptor, record = _spot()
    runner = FakeSharedRunner((descriptor.key,))
    manager = SharedIndexRunnerLocalFeedManager(runner)
    manager.attach()
    feed = CommonStrategyMarketFeed("box-local-consumer", manager=manager)
    received = []
    arrived = Event()
    try:
        feed.start((descriptor,), lambda item: (received.append(item), arrived.set()))
        runner.publish(record)
        assert arrived.wait(1.0)
        assert received == [record]
        snapshot = manager.snapshot()
        assert snapshot["attached"] is True
        assert snapshot["consumer_count"] == 1
        assert snapshot["records_dispatched"] == 1
    finally:
        feed.stop()
        manager.close()


def test_local_feed_rejects_instruments_not_owned_by_shared_runner():
    descriptor, _record = _spot()
    runner = FakeSharedRunner(())
    manager = SharedIndexRunnerLocalFeedManager(runner)
    manager.attach()
    feed = CommonStrategyMarketFeed("box-invalid-universe", manager=manager)
    try:
        with pytest.raises(PermissionError, match="currently owned by SharedIndexMarketDataRunner"):
            feed.start((descriptor,), lambda _item: None)
        assert manager.snapshot()["consumer_count"] == 0
        assert manager.registry.get(descriptor.key) is None
    finally:
        feed.stop()
        manager.close()


def test_local_fanout_evicts_oldest_tick_when_bounded_queue_is_full():
    descriptor, first = _spot()
    runner = FakeSharedRunner((descriptor.key,))
    manager = SharedIndexRunnerLocalFeedManager(runner, queue_capacity=1)
    manager.attach()
    manager._ensure_worker_locked = lambda: None
    feed = CommonStrategyMarketFeed("box-queue-test", manager=manager)
    try:
        feed.start((descriptor,), lambda _item: None)
        second = MarketDataRecord(
            instrument=descriptor.key,
            symbol="NIFTY",
            instrument_type=InstrumentType.INDEX,
            timestamp_ns=first.timestamp_ns + 1,
            ltp=24001.0,
            underlying="NIFTY",
        )
        runner.publish(first)
        runner.publish(second)
        snapshot = manager.snapshot()
        assert snapshot["queue_depth"] == 1
        assert snapshot["queue_dropped"] == 1
        queued = manager._queue.get_nowait()
        manager._queue.task_done()
        assert queued is second
    finally:
        feed.stop()
        manager.close()
