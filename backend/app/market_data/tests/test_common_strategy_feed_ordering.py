from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.contracts import InstrumentType, MarketDataRecord
from app.market_data.shared_cache import get_shared_market_data_cache


class FakeRegistry:
    def get(self, key):
        return None

    def register_many(self, descriptors):
        self.descriptors = tuple(descriptors)


class FakeManager:
    def __init__(self):
        self.registry = FakeRegistry()
        self.callback = None

    def register_normalized_callback(self, consumer, callback):
        self.callback = callback

    def subscribe(self, consumer, keys, mode=3):
        return None

    def clear_consumer(self, consumer):
        return None


def test_equal_timestamp_latest_arrival_is_delivered_but_older_tick_is_rejected():
    get_shared_market_data_cache().clear()
    manager = FakeManager()
    feed = CommonStrategyMarketFeed("ordering-test", manager=manager)
    descriptor = feed.descriptor(
        exchange="NFO",
        token="FUT-1",
        symbol="ABC-FUT",
        instrument_type="future",
    )
    received = []
    feed.start([descriptor], received.append)

    def record(timestamp_ns, ltp):
        return MarketDataRecord(
            instrument=descriptor.key,
            symbol=descriptor.symbol,
            instrument_type=InstrumentType.FUTURE,
            timestamp_ns=timestamp_ns,
            ltp=ltp,
        )

    manager.callback(record(1_000, 100.0))
    manager.callback(record(1_000, 101.0))
    manager.callback(record(999, 99.0))

    assert [(item.timestamp_ns, item.ltp) for item in received] == [
        (1_000, 100.0),
        (1_000, 101.0),
    ]
    feed.stop()
