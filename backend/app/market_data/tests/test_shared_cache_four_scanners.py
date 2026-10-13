"""Integration regression: shared normalized snapshots are visible to all scanner consumers."""
from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry
from app.market_data.shared_cache import get_shared_market_data_cache, market_data_cache_key


class CacheTestSocket:
    instances = []

    def __init__(self):
        self.connected = False
        self.connecting = False
        self.closed = False
        self.connect_calls = []
        self.subscribe_calls = []
        type(self).instances.append(self)

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        self.connected = True

    def subscribe(self, tokens, mode=None):
        self.subscribe_calls.append((list(tokens), mode))

    def subscribe_groups(self, groups, mode=None):
        self.subscribe_calls.append((dict(groups), mode))

    def close(self):
        self.closed = True
        self.connected = False


def test_four_scanner_callbacks_see_same_cached_quote_before_delivery():
    key = InstrumentKey("NFO", "NFO", "cache-four-scanners-1")
    descriptor = InstrumentDescriptor(
        key=key, symbol="CACHE_TEST", instrument_type="equity",
        exchange="NFO", segment="NFO",
    )
    registry = InstrumentRegistry()
    registry.register(descriptor)
    manager = CommonWebSocketManager(registry, socket_factory=CacheTestSocket)
    cache = get_shared_market_data_cache()
    cache_key = market_data_cache_key(key.exchange, key.segment, key.token)
    cache.delete(cache_key)
    seen = {}
    names = ("cash-future", "synthetic-future", "calendar-spread", "box-spread")

    try:
        for name in names:
            manager.register_normalized_callback(
                name, lambda record, consumer=name: seen.__setitem__(consumer, cache.get(cache_key))
            )
            manager.subscribe(name, [key])
        assert len(CacheTestSocket.instances) == 1
        manager._on_data(
            SocketGroup(1, 0),
            {"token": key.token, "exchange_type": 2, "ltp": 98765, "timestamp_ns": 1_800_000_000_000_000_001},
        )
        assert set(seen) == set(names)
        snapshots = list(seen.values())
        assert all(snapshot is not None for snapshot in snapshots)
        assert all(snapshot["token"] == key.token for snapshot in snapshots)
        assert len({snapshot["timestamp_ns"] for snapshot in snapshots}) == 1
        assert {snapshot["ltp"] for snapshot in snapshots} == {987.65}
    finally:
        manager.close()
        cache.delete(cache_key)
