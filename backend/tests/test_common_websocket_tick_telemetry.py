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
    # _on_data intentionally ignores ticks from absent/stale socket groups.
    # Model an active socket generation as the production connect callback does.
    manager._sockets[group] = object()
    manager._socket_generation[group] = 1
    manager._normalizer.normalize = lambda _descriptor, _message: SimpleNamespace(
        instrument=key, symbol="TEST", timestamp_ns=123456789, ltp=101.5, bid=101.4, ask=101.6,
    )

    manager.register_normalized_callback("test", lambda _record: None)

    manager._on_data(group, {"token": "123", "exchange_type": 1}, generation=1)

    snapshot = manager.snapshot()
    assert snapshot["ticks_received"] == 1
    assert snapshot["ticks_by_exchange_type"] == {"1": 1}
    assert snapshot["last_tick"] == {
        "exchange_type": 1, "exchange": "NSE", "segment": "NSE", "token": "123",
        "symbol": "TEST", "timestamp_ns": 123456789, "ltp": 101.5, "bid": 101.4, "ask": 101.6,
    }


def test_angel_normalized_observer_receives_generation_but_auth_fails_closed():
    key = InstrumentKey("NSE", "NSE", "123")
    descriptor = InstrumentDescriptor(
        key=key, symbol="TEST", instrument_type="equity", exchange="NSE", segment="NSE",
    )
    manager = CommonWebSocketManager(socket_factory=lambda: None)
    manager.registry.register(descriptor)
    manager.registry.subscribe("observer", key, mode=3)
    group = SocketGroup(3, 0)
    manager._route_index = {group: {(1, "123"): [("observer", key)]}}
    manager._sockets[group] = object()
    manager._socket_generation[group] = 9
    manager._normalizer.normalize = lambda _descriptor, _message: SimpleNamespace(
        instrument=key, symbol="TEST", timestamp_ns=123456789, ltp=101.5, bid=101.4, ask=101.6,
    )
    observed = []
    manager.register_normalized_observer("observer", lambda *args: observed.append(args))

    manager._on_data(group, {"token": "123", "exchange_type": 1}, generation=9)

    assert len(observed) == 1
    record, observed_group, generation, authenticated, current, received_ns = observed[0]
    assert record.symbol == "TEST"
    assert observed_group == group
    assert generation == 9
    assert authenticated is False
    assert current is True
    assert received_ns > 0


def test_angel_observer_is_not_called_for_retired_generation():
    key = InstrumentKey("NSE", "NSE", "123")
    descriptor = InstrumentDescriptor(
        key=key, symbol="TEST", instrument_type="equity", exchange="NSE", segment="NSE",
    )
    manager = CommonWebSocketManager(socket_factory=lambda: None)
    manager.registry.register(descriptor)
    manager.registry.subscribe("observer", key, mode=3)
    group = SocketGroup(3, 0)
    manager._route_index = {group: {(1, "123"): [("observer", key)]}}
    manager._sockets[group] = object()
    manager._socket_generation[group] = 10
    observed = []
    manager.register_normalized_observer("observer", lambda *args: observed.append(args))

    manager._on_data(group, {"token": "123", "exchange_type": 1}, generation=9)

    assert observed == []


def test_angel_observer_failure_does_not_break_legacy_normalized_callback():
    key = InstrumentKey("NSE", "NSE", "123")
    descriptor = InstrumentDescriptor(
        key=key, symbol="TEST", instrument_type="equity", exchange="NSE", segment="NSE",
    )
    manager = CommonWebSocketManager(socket_factory=lambda: None)
    manager.registry.register(descriptor)
    manager.registry.subscribe("legacy", key, mode=3)
    manager.registry.subscribe("observer", key, mode=3)
    group = SocketGroup(3, 0)
    manager._route_index = {group: {(1, "123"): [("legacy", key), ("observer", key)]}}
    manager._sockets[group] = object()
    manager._socket_generation[group] = 2
    manager._normalizer.normalize = lambda _descriptor, _message: SimpleNamespace(
        instrument=key, symbol="TEST", timestamp_ns=123456789, ltp=101.5, bid=101.4, ask=101.6,
    )
    delivered = []
    manager.register_normalized_callback("legacy", delivered.append)
    manager.register_normalized_observer("observer", lambda *_: (_ for _ in ()).throw(RuntimeError("observer fault")))

    manager._on_data(group, {"token": "123", "exchange_type": 1}, generation=2)

    assert len(delivered) == 1
    assert delivered[0].symbol == "TEST"
    assert manager.snapshot()["normalized_records_delivered"] == 1
