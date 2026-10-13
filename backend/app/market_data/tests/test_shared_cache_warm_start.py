from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.shared_cache import get_shared_market_data_cache


class FakeSocket:
    def __init__(self):
        self.connect_calls = []
        self.connected = False
        self.connecting = False
        self.closed = False

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        self.connected = True
        self.connecting = False

    def subscribe(self, tokens, mode=None):
        pass

    def subscribe_groups(self, groups, mode=None):
        pass

    def unsubscribe_groups(self, groups):
        pass

    def close(self):
        self.closed = True
        self.connected = False


def test_common_strategy_feed_warm_starts_from_shared_snapshot_then_accepts_newer_tick():
    cache = get_shared_market_data_cache()
    cache.clear()
    descriptor = CommonStrategyMarketFeed.descriptor(
        exchange="NSE",
        token="101",
        symbol="TEST-EQ",
        instrument_type="equity",
        segment="NSE",
    )
    cache.put(
        descriptor.key.value,
        {
            "exchange": "NSE",
            "segment": "NSE",
            "token": "101",
            "symbol": "TEST-EQ",
            "instrument_type": "equity",
            "timestamp_ns": 1000,
            "ltp": 100.0,
            "payload": {"source": "cache"},
        },
        timestamp_ns=1000,
    )
    manager = CommonWebSocketManager(socket_factory=FakeSocket)
    manager._recovery_interval_seconds = 60.0
    feed = CommonStrategyMarketFeed("cache-warm-start-test", manager=manager)
    received = []
    try:
        feed.start([descriptor], received.append)
        assert [(record.timestamp_ns, record.ltp) for record in received] == [(1000, 100.0)]

        manager._on_data(
            SocketGroup(3, 0),
            {
                "token": "101",
                "exchange_type": 1,
                "timestamp_ns": 2000,
                "last_traded_price": 12345,
            },
        )
        assert [(record.timestamp_ns, record.ltp) for record in received] == [
            (1000, 100.0),
            (2000, 123.45),
        ]
        assert cache.get(descriptor.key.value)["timestamp_ns"] == 2000
    finally:
        feed.stop()
        manager.close()
        cache.clear()


def test_invalid_shared_snapshot_is_skipped_without_blocking_subscription():
    cache = get_shared_market_data_cache()
    cache.clear()
    descriptor = CommonStrategyMarketFeed.descriptor(
        exchange="NSE",
        token="102",
        symbol="TEST-EQ-2",
        instrument_type="equity",
        segment="NSE",
    )
    cache.put(descriptor.key.value, {"timestamp_ns": "not-an-int", "ltp": 100.0})
    manager = CommonWebSocketManager(socket_factory=FakeSocket)
    manager._recovery_interval_seconds = 60.0
    feed = CommonStrategyMarketFeed("cache-invalid-snapshot-test", manager=manager)
    received = []
    try:
        feed.start([descriptor], received.append)
        assert received == []
        assert manager.snapshot()["socket_groups"] == 1
    finally:
        feed.stop()
        manager.close()
        cache.clear()
