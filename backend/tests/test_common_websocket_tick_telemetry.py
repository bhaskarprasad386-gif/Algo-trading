from types import SimpleNamespace

from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor
from app.market_data.shared_cache import get_shared_market_data_cache, market_data_cache_key


def test_common_feed_tracks_in_memory_tick_telemetry_without_persistence():
    key = InstrumentKey("NSE", "NSE", "123")
    descriptor = InstrumentDescriptor(
        key=key, symbol="TEST", instrument_type="equity", exchange="NSE", segment="NSE",
    )
    manager = CommonWebSocketManager(socket_factory=lambda: None)
    manager.registry.register(descriptor)
    manager.registry.subscribe("test", key, mode=3)
    group = SocketGroup(3, 0)
    manager._route_index = {group: {(1, "123"): [("test", key)]}}
    # _on_data intentionally ignores ticks from absent/stale socket groups.
    # Model an active socket generation as the production connect callback does.
    manager._sockets[group] = object()
    manager._socket_generation[group] = 1

    record = SimpleNamespace(
        instrument=key,
        symbol="TEST",
        timestamp_ns=123456789,
        ltp=101.5,
        bid=101.4,
        ask=101.6,
        as_dict=lambda: {
            "exchange": "NSE",
            "segment": "NSE",
            "token": "123",
            "symbol": "TEST",
            "timestamp_ns": 123456789,
            "ltp": 101.5,
            "bid": 101.4,
            "ask": 101.6,
            "payload": {"raw": "omitted"},
        },
    )
    manager._normalizer.normalize = lambda _descriptor, _message: record

    cache = get_shared_market_data_cache()
    cache.clear()
    delivered = []
    manager.register_normalized_callback("test", delivered.append)

    manager._on_data(group, {"token": "123", "exchange_type": 1}, generation=1)

    cached = cache.get(market_data_cache_key("NSE", "NSE", "123"))
    assert cached is not None
    assert cached["ltp"] == 101.5
    assert cached["timestamp_ns"] == 123456789
    assert "payload" not in cached
    assert delivered == [record]

    snapshot = manager.snapshot()
    assert snapshot["ticks_received"] == 1
    assert snapshot["ticks_by_exchange_type"] == {"1": 1}
    assert snapshot["last_tick"] == {
        "exchange_type": 1, "exchange": "NSE", "segment": "NSE", "token": "123",
        "symbol": "TEST", "timestamp_ns": 123456789, "ltp": 101.5, "bid": 101.4, "ask": 101.6,
    }
    cache.delete(market_data_cache_key("NSE", "NSE", "123"))
