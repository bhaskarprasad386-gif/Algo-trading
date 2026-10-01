from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry


class FakeSocket:
    instances = []

    def __init__(self):
        self.connect_calls = []
        self.subscribe_calls = []
        self.closed = False
        FakeSocket.instances.append(self)

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)

    def subscribe(self, tokens, mode=None):
        self.subscribe_calls.append((list(tokens), mode))

    def subscribe_groups(self, groups, mode=None):
        self.subscribe_calls.append((dict(groups), mode))

    def unsubscribe_groups(self, groups):
        pass

    def close(self):
        self.closed = True


def descriptor(token, exchange="NSE", segment="EQ"):
    key = InstrumentKey(exchange, segment, token)
    return InstrumentDescriptor(
        key=key,
        symbol=f"S{token}",
        instrument_type="equity",
        exchange=exchange,
        segment=segment,
    )


def setup_function():
    FakeSocket.instances.clear()


def test_common_manager_deduplicates_shared_instrument_and_fans_out():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    seen = []
    manager.register_callback("cash", lambda m: seen.append(("cash", m["token"])))
    manager.register_callback("box", lambda m: seen.append(("box", m["token"])))

    manager.subscribe("cash", [d.key])
    manager.subscribe("box", [d.key])

    assert len(FakeSocket.instances) == 1
    socket = FakeSocket.instances[0]
    assert len(socket.connect_calls) == 1
    assert socket.connect_calls[0]["subscriptions"] == {1: ["101"]}
    assert registry.subscriptions()[0].ref_count == 2

    manager._on_data(SocketGroup(1, 0), {"token": "101", "ltp": 100})
    assert set(seen) == {("box", "101"), ("cash", "101")}


def test_mode_upgrade_rehomes_socket_group_without_duplicate_sockets():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)

    manager.subscribe("a", [d.key], mode=1)
    first = FakeSocket.instances[0]
    manager.subscribe("b", [d.key], mode=3)

    assert len(FakeSocket.instances) == 2
    assert first.closed is True
    assert FakeSocket.instances[1].connect_calls[0]["mode"] == 3
    assert registry.subscriptions()[0].mode == 3


def test_clear_consumer_closes_unused_socket_and_preserves_other_consumer():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    manager.register_callback("a", lambda m: None)
    manager.register_callback("b", lambda m: None)
    manager.subscribe("a", [d.key])
    manager.subscribe("b", [d.key])

    manager.clear_consumer("a")
    assert registry.subscriptions()[0].consumers == frozenset({"b"})
    assert FakeSocket.instances[0].closed is False

    manager.clear_consumer("b")
    assert manager.snapshot()["active_instruments"] == 0
    assert FakeSocket.instances[0].closed is True


def test_same_token_on_different_exchange_group_does_not_cross_fan_out():
    registry = InstrumentRegistry()
    nse = descriptor("101", "NSE", "EQ")
    nfo = descriptor("101", "NFO", "DERIVATIVES")
    registry.register_many([nse, nfo])
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    seen = []
    manager.register_callback("nse", lambda m: seen.append("nse"))
    manager.register_callback("nfo", lambda m: seen.append("nfo"))
    manager.subscribe("nse", [nse.key])
    manager.subscribe("nfo", [nfo.key])

    manager._on_data(SocketGroup(1, 0), {"token": "101", "exchange_type": 1})
    assert seen == ["nse"]
