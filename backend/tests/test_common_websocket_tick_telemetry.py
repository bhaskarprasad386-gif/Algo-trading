from types import SimpleNamespace

from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor


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
    manager._normalizer.normalize = lambda _descriptor, _message: SimpleNamespace(
        instrument=key, symbol="TEST", timestamp_ns=123456789, ltp=101.5, bid=101.4, ask=101.6,
    )

    manager.register_normalized_callback("test", lambda _record: None)

    manager._on_data(group, {"token": "123", "exchange_type": 1})

    snapshot = manager.snapshot()
    assert snapshot["ticks_received"] == 1
    assert snapshot["ticks_by_exchange_type"] == {"1": 1}
    assert snapshot["last_tick"] == {
        "exchange_type": 1, "exchange": "NSE", "segment": "NSE", "token": "123",
        "symbol": "TEST", "timestamp_ns": 123456789, "ltp": 101.5, "bid": 101.4, "ask": 101.6,
    }
