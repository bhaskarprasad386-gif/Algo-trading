from threading import Event
from time import time_ns
from types import SimpleNamespace

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.registry import InstrumentDescriptor
from app.market_data.shared_index_local_feed import SharedIndexRunnerLocalFeedManager


class FakeSharedRunner:
    def __init__(self):
        self.state = SimpleNamespace(
            _publish=lambda _record: None,
            _allowed_keys=None,
            is_instrument_allowed=lambda key: self.state._allowed_keys is not None and key.value in self.state._allowed_keys,
        )

    def publish(self, record):
        self.state._publish(record)

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
        timestamp_ns=time_ns(),
        ltp=24000.0,
        underlying="NIFTY",
    )
    return descriptor, record


def test_local_feed_delivers_shared_runner_records_without_broker_manager():
    runner = FakeSharedRunner()
    manager = SharedIndexRunnerLocalFeedManager(runner)
    manager.attach()
    descriptor, record = _spot()
    runner.state._allowed_keys = {descriptor.key.value}
    received = Event()
    observed = []

    feed = CommonStrategyMarketFeed("box-underlyings", manager=manager)
    try:
        feed.start((descriptor,), lambda item: (observed.append(item), received.set()))
        runner.publish(record)

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
    manager = SharedIndexRunnerLocalFeedManager(runner)
    manager.attach()
    descriptor, record = _spot()
    runner.state._allowed_keys = {descriptor.key.value}
    first = Event()
    second = Event()
    update_received = Event()
    observed = []

    one = CommonStrategyMarketFeed("box-underlyings", manager=manager)
    two = CommonStrategyMarketFeed("box-depth", manager=manager)
    try:
        one.start((descriptor,), lambda item: (observed.append(("one", item)), first.set()))
        def receive_two(item):
            observed.append(("two", item))
            second.set()
            if item.timestamp_ns > record.timestamp_ns:
                update_received.set()

        two.start((descriptor,), receive_two)
        runner.publish(record)

        assert first.wait(1.0)
        assert second.wait(1.0)
        assert len(observed) == 2

        one.stop()
        observed.clear()
        runner.publish(MarketDataRecord(
            instrument=descriptor.key,
            symbol="NIFTY",
            instrument_type=InstrumentType.INDEX,
            timestamp_ns=record.timestamp_ns + 1,
            ltp=24001.0,
            underlying="NIFTY",
        ))
        assert update_received.wait(1.0)
        assert len(observed) == 1
        assert observed[0][0] == "two"
    finally:
        one.stop()
        two.stop()
        manager.close()


def test_local_fanout_does_not_buffer_ticks_without_box_consumers():
    runner = FakeSharedRunner()
    manager = SharedIndexRunnerLocalFeedManager(runner)
    manager.attach()
    _descriptor, record = _spot()

    try:
        runner.publish(record)
        assert manager.snapshot()["queue_depth"] == 0
        assert manager.snapshot()["queue_dropped"] == 0
    finally:
        manager.close()


def test_local_fanout_can_reattach_after_application_lifespan_shutdown():
    runner = FakeSharedRunner()
    manager = SharedIndexRunnerLocalFeedManager(runner)
    descriptor, record = _spot()
    received = Event()

    manager.attach()
    manager.close()
    manager.attach()
    runner.state._allowed_keys = {descriptor.key.value}
    feed = CommonStrategyMarketFeed("box-after-restart", manager=manager)
    try:
        feed.start((descriptor,), lambda _item: received.set())
        runner.publish(record)
        assert received.wait(1.0)
    finally:
        feed.stop()
        manager.close()


def test_local_feed_rejects_instruments_not_owned_by_shared_runner():
    runner = FakeSharedRunner()
    manager = SharedIndexRunnerLocalFeedManager(runner)
    manager.attach()
    descriptor, _record = _spot()
    try:
        feed = CommonStrategyMarketFeed("box-invalid-universe", manager=manager)
        try:
            try:
                feed.start((descriptor,), lambda _item: None)
            except PermissionError as exc:
                assert "currently owned by SharedIndexMarketDataRunner" in str(exc)
            else:
                raise AssertionError("unowned instrument subscription must fail closed")
            assert manager.snapshot()["consumer_count"] == 0
            assert manager.registry.get(descriptor.key) is None
        finally:
            feed.stop()
    finally:
        manager.close()
