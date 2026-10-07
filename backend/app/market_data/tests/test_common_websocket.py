from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry
import time


class FakeSocket:
    instances = []

    def __init__(self):
        self.connect_calls = []
        self.subscribe_calls = []
        self.closed = False
        self.connected = False
        self.connecting = False
        FakeSocket.instances.append(self)

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        self.connecting = False
        self.connected = True

    def subscribe(self, tokens, mode=None):
        self.subscribe_calls.append((list(tokens), mode))

    def subscribe_groups(self, groups, mode=None):
        self.subscribe_calls.append((dict(groups), mode))

    def unsubscribe_groups(self, groups):
        pass

    def close(self):
        self.closed = True
        self.connected = False


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
    snapshot = manager.snapshot()
    assert snapshot["socket_groups"] == 1
    assert snapshot["disconnected_groups"] == []
    assert snapshot["connected_groups"] == ["1:0"]

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


def test_recovery_rebuilds_missing_socket_shard():
    registry = InstrumentRegistry()
    descriptors = [descriptor(str(i)) for i in range(1001)]
    registry.register_many(descriptors)
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    manager.subscribe("cash", [d.key for d in descriptors])

    assert len(FakeSocket.instances) == 2
    missing = SocketGroup(1, 1)
    with manager._lock:
        manager._sockets.pop(missing)
        manager._socket_tokens.pop(missing, None)
        manager._socket_created_at.pop(missing, None)

    recovered = manager.recover_disconnected(min_age_seconds=10.0)

    assert recovered == 0
    assert len(FakeSocket.instances) == 3
    assert manager.snapshot()["socket_groups"] == 2
    assert manager.snapshot()["connected_groups"] == ["1:0", "1:1"]


class PartialFailureSocket(FakeSocket):
    created = 0

    def __init__(self):
        super().__init__()
        type(self).created += 1

    def connect(self, **kwargs):
        if type(self).created == 2:
            raise RuntimeError("simulated second-shard connect failure")
        return super().connect(**kwargs)


def test_partial_reconcile_failure_is_recovered_on_next_cycle():
    registry = InstrumentRegistry()
    descriptors = [descriptor(str(i)) for i in range(1001)]
    registry.register_many(descriptors)
    manager = CommonWebSocketManager(registry, socket_factory=PartialFailureSocket)
    manager._recovery_interval_seconds = 60.0
    try:
        try:
            manager.subscribe("cash", [d.key for d in descriptors])
        except RuntimeError as exc:
            assert "second-shard" in str(exc)
        else:
            raise AssertionError("expected partial shard connection failure")

        assert manager.snapshot()["socket_groups"] == 1
        assert len(registry.subscriptions()) == 1001

        PartialFailureSocket.created = 2
        recovered = manager.recover_disconnected(min_age_seconds=0.0)

        assert recovered == 0
        snapshot = manager.snapshot()
        assert snapshot["socket_groups"] == 2
        assert snapshot["connected_groups"] == ["1:0", "1:1"]
    finally:
        manager.close()
        PartialFailureSocket.created = 0


def test_recovery_supervisor_rebuilds_missing_socket_without_strategy_runner():
    registry = InstrumentRegistry()
    descriptors = [descriptor(str(i)) for i in range(1001)]
    registry.register_many(descriptors)
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    manager._recovery_interval_seconds = 0.01
    try:
        manager.subscribe("cash", [d.key for d in descriptors])
        assert manager.snapshot()["socket_groups"] == 2
        missing = SocketGroup(1, 1)
        with manager._lock:
            manager._sockets.pop(missing)
            manager._socket_tokens.pop(missing, None)
            manager._socket_created_at.pop(missing, None)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and manager.snapshot()["socket_groups"] < 2:
            time.sleep(0.02)
        assert manager.snapshot()["socket_groups"] == 2
    finally:
        manager.close()


class AlwaysFailThenRecoverSocket(FakeSocket):
    attempts = 0

    def connect(self, **kwargs):
        type(self).attempts += 1
        if type(self).attempts == 1:
            raise ConnectionError("simulated broker connect failure")
        return super().connect(**kwargs)


def test_connect_failure_telemetry_and_recovery_restores_socket():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=AlwaysFailThenRecoverSocket)
    manager._recovery_interval_seconds = 60.0
    try:
        try:
            manager.subscribe("cash", [d.key])
        except ConnectionError:
            pass
        else:
            raise AssertionError("expected simulated broker connect failure")

        failed = manager.snapshot()
        assert failed["socket_groups"] == 0
        assert failed["connect_failures"] == 1
        assert failed["last_connect_failure"]["group"] == "1:0"
        assert failed["last_connect_failure"]["error_type"] == "ConnectionError"
        assert "simulated broker connect failure" in failed["last_connect_failure"]["error"]

        manager.recover_disconnected(min_age_seconds=0.0)

        recovered = manager.snapshot()
        assert recovered["socket_groups"] == 1
        assert recovered["connected_groups"] == ["1:0"]
        assert recovered["recovery_attempts"] == 1
        assert recovered["last_recovery_at"] is not None
    finally:
        manager.close()
        AlwaysFailThenRecoverSocket.attempts = 0



def test_recovery_replaces_connected_silent_socket():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    manager._silent_feed_timeout_seconds = 0.0
    try:
        manager.subscribe("cash", [d.key])
        original = FakeSocket.instances[0]
        # Recovery keeps a one-second minimum socket age even when callers pass
        # min_age_seconds=0.0; age the fake socket so the test targets silence.
        with manager._lock:
            manager._socket_created_at[SocketGroup(1, 0)] -= 2.0

        recovered = manager.recover_disconnected(
            min_age_seconds=0.0,
            silent_age_seconds=0.0,
        )

        assert recovered == 1
        assert original.closed is True
        assert len(FakeSocket.instances) == 2
        assert manager.snapshot()["socket_groups"] == 1
        assert manager.snapshot()["connected_groups"] == ["1:0"]
    finally:
        manager.close()

class ConnectingSocket(FakeSocket):
    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        self.connecting = True
        self.connected = False


def test_recovery_does_not_reap_async_connecting_socket():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=ConnectingSocket)
    manager.subscribe("cash", [d.key])

    recovered = manager.recover_disconnected(min_age_seconds=0.0)

    assert recovered == 0
    assert len(ConnectingSocket.instances) == 1
    assert manager.snapshot()["socket_groups"] == 1
    manager.close()
