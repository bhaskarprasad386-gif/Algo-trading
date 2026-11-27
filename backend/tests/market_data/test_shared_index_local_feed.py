from threading import Event

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.registry import InstrumentDescriptor
from app.market_data.shared_index_local_feed import SharedIndexRunnerLocalFeedManager


class FakeSharedRunner:
    def __init__(self):
        self.records = {}

    def latest(self, key):
        return self.records.get(key)

    def snapshot(self):
        return {"enabled": True, "source": "fake-shared-runner"}


def _spot():
    descriptor = InstrumentDescriptor(
        key=InstrumentKey("NSE", "NSE", "99001"),
        symbol="NIFTY",
        instrument_type="index",
        exchange="NSE",
        segment="NSE",
        underlying="NIFTY",
    )
    record = MarketDataRecord(
        instrument=descriptor.key,
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=1_900_000_000_000_000_000,
        ltp=24000.0,
        underlying="NIFTY",
    )
    return descriptor, record


def test_local_feed_delivers_shared_runner_records_without_broker_manager():
    runner = FakeSharedRunner()
    manager = SharedIndexRunnerLocalFeedManager(runner, poll_seconds=0.005)
    descriptor, record = _spot()
    received = Event()
    observed = []

    feed = CommonStrategyMarketFeed("box-underlyings", manager=manager)
    try:
        feed.start((descriptor,), lambda item: (observed.append(item), received.set()))
        runner.records[descriptor.key.value] = record

        assert received.wait(1.0)
        assert observed == [record]
        assert manager.snapshot()["delivered_records"] == 1
        assert manager.snapshot()["instrument_count"] == 1
    finally:
        feed.stop()
        manager.close()

    assert manager.snapshot()["instrument_count"] == 0


def test_local_feed_deduplicates_ticks_and_clears_only_requested_consumer():
    runner = FakeSharedRunner()
    manager = SharedIndexRunnerLocalFeedManager(runner, poll_seconds=0.005)
    descriptor, record = _spot()
    first = Event()
    second = Event()
    observed = []

    one = CommonStrategyMarketFeed("box-underlyings", manager=manager)
    two = CommonStrategyMarketFeed("box-depth", manager=manager)
    try:
        one.start((descriptor,), lambda item: (observed.append(("one", item)), first.set()))
        two.start((descriptor,), lambda item: (observed.append(("two", item)), second.set()))
        runner.records[descriptor.key.value] = record

        assert first.wait(1.0)
        assert second.wait(1.0)
        assert len(observed) == 2

        one.stop()
        observed.clear()
        runner.records[descriptor.key.value] = MarketDataRecord(
            instrument=descriptor.key,
            symbol="NIFTY",
            instrument_type=InstrumentType.INDEX,
            timestamp_ns=record.timestamp_ns + 1,
            ltp=24001.0,
            underlying="NIFTY",
        )
        assert Event().wait(0.02)
        assert len(observed) == 1
        assert observed[0][0] == "two"
    finally:
        one.stop()
        two.stop()
        manager.close()
