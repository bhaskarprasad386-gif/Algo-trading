from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.shared_cache import get_shared_market_data_cache


class FakeSocket:
    def __init__(self):
        self.connected = False
        self.connecting = False

    def connect(self, **kwargs):
        self.connected = True
        self.connecting = False

    def subscribe(self, tokens, mode=None):
        pass

    def subscribe_groups(self, groups, mode=None):
        pass

    def unsubscribe_groups(self, groups):
        pass

    def close(self):
        self.connected = False


def test_common_strategy_feed_warm_starts_then_accepts_newer_live_tick():
    cache = get_shared_market_data_cache()
    cache.clear()
    descriptor = CommonStrategyMarketFeed.descriptor(
        exchange="NSE", token="cache-warm-start-101", symbol="TEST-EQ",
        instrument_type="equity", segment="NSE",
    )
    cache.put(descriptor.key.value, {"timestamp_ns": 1000, "ltp": 100.0}, timestamp_ns=1000)
    manager = CommonWebSocketManager(socket_factory=FakeSocket)
    manager._recovery_interval_seconds = 60.0
    feed = CommonStrategyMarketFeed("cache-warm-start-test", manager=manager)
    received = []
    try:
        feed.start([descriptor], received.append)
        assert [(r.timestamp_ns, r.ltp) for r in received] == [(1000, 100.0)]
        manager._on_data(SocketGroup(1, 0), {
            "token": descriptor.key.token, "exchange_type": 1,
            "timestamp_ns": 2000, "last_traded_price": 12345,
        })
        assert [(r.timestamp_ns, r.ltp) for r in received] == [(1000, 100.0), (2000, 123.45)]
        assert cache.get(descriptor.key.value)["timestamp_ns"] == 2000

        # A delayed broker frame must not overwrite the newer scanner snapshot.
        manager._on_data(SocketGroup(1, 0), {
            "token": descriptor.key.token, "exchange_type": 1,
            "timestamp_ns": 1500, "last_traded_price": 11000,
        })
        assert [(r.timestamp_ns, r.ltp) for r in received] == [(1000, 100.0), (2000, 123.45)]
        assert cache.get(descriptor.key.value)["timestamp_ns"] == 2000
    finally:
        feed.stop()
        manager.close()
        cache.clear()


def test_invalid_snapshot_does_not_block_live_subscription():
    cache = get_shared_market_data_cache()
    cache.clear()
    descriptor = CommonStrategyMarketFeed.descriptor(
        exchange="NSE", token="cache-warm-start-102", symbol="TEST-EQ-2",
        instrument_type="equity", segment="NSE",
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


def test_option_warm_start_preserves_box_contract_metadata():
    cache = get_shared_market_data_cache()
    cache.clear()
    descriptor = CommonStrategyMarketFeed.descriptor(
        exchange="NFO", token="991827103", symbol="NIFTY26NOV26000CE",
        instrument_type="option", segment="NFO", expiry="2026-11-26",
        strike=26000, option_type="CE", lot_size=65, tick_size=0.05,
        underlying="NIFTY",
    )
    cache.put(
        descriptor.key.value,
        {"timestamp_ns": 3000, "ltp": 125.5, "bid": 125.0, "ask": 126.0,
         "bid_qty": 130, "ask_qty": 195, "oi": 250000},
        timestamp_ns=3000,
    )
    manager = CommonWebSocketManager(socket_factory=FakeSocket)
    manager._recovery_interval_seconds = 60.0
    feed = CommonStrategyMarketFeed("box-option-warm-start-test", manager=manager)
    received = []
    try:
        feed.start([descriptor], received.append)
        assert len(received) == 1
        record = received[0]
        assert record.timestamp_ns == 3000
        assert record.ltp == 125.5
        assert record.bid == 125.0 and record.ask == 126.0
        assert record.bid_qty == 130 and record.ask_qty == 195
        assert record.oi == 250000
        assert record.expiry == "2026-11-26"
        assert record.strike == 26000
        assert str(record.option_type.value) == "CE"
        assert record.lot_size == 65
        assert record.tick_size == 0.05
        assert record.underlying == "NIFTY"
    finally:
        feed.stop()
        manager.close()
        cache.clear()
