from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.websocket import MarketDataWebSocket
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry
from threading import Event, Thread
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


def test_stale_socket_callback_is_ignored_after_recovery_replaces_socket():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    seen = []
    manager.register_callback("cash", lambda m: seen.append(m["token"]))
    manager._silent_feed_timeout_seconds = 0.0
    try:
        manager.subscribe("cash", [d.key])
        original = FakeSocket.instances[0]
        old_callback = original.connect_calls[0]["on_data"]
        with manager._lock:
            manager._socket_created_at[SocketGroup(1, 0)] -= 2.0

        manager.recover_disconnected(min_age_seconds=0.0, silent_age_seconds=0.0)
        replacement = FakeSocket.instances[1]
        new_callback = replacement.connect_calls[0]["on_data"]

        old_callback({"token": "101"})
        assert seen == []
        assert manager.snapshot()["ticks_received"] == 0

        new_callback({"token": "101"})
        assert seen == ["101"]
        assert manager.snapshot()["ticks_received"] == 1
    finally:
        manager.close()


def test_recovery_replaces_socket_stuck_in_connecting_state_after_timeout():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=ConnectingSocket)
    manager._connect_timeout_seconds = 1.0
    manager._recovery_interval_seconds = 60.0
    try:
        manager.subscribe("cash", [d.key])
        original = ConnectingSocket.instances[0]
        with manager._lock:
            manager._socket_created_at[SocketGroup(1, 0)] -= 2.0

        recovered = manager.recover_disconnected(min_age_seconds=0.0)

        assert recovered == 1
        assert original.closed is True
        assert len(ConnectingSocket.instances) == 2
        assert manager.snapshot()["socket_groups"] == 1
        assert manager.snapshot()["disconnected_groups"] == ["1:0"]
    finally:
        manager.close()


class BlockingFirstSocket(FakeSocket):
    attempts = 0
    release_first_connect = Event()

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        type(self).attempts += 1
        if type(self).attempts == 1:
            self.connecting = True
            type(self).release_first_connect.wait(timeout=5.0)
            return
        super().connect(**kwargs)


def test_blocking_connect_does_not_hold_recovery_reconcile_lock():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    BlockingFirstSocket.attempts = 0
    BlockingFirstSocket.release_first_connect.clear()
    manager = CommonWebSocketManager(registry, socket_factory=BlockingFirstSocket)
    manager._connect_call_wait_seconds = 0.05
    manager._connect_timeout_seconds = 1.0
    manager._recovery_interval_seconds = 60.0
    try:
        started = time.monotonic()
        manager.subscribe("cash", [d.key])
        assert time.monotonic() - started < 1.0

        original = BlockingFirstSocket.instances[0]
        with manager._lock:
            manager._socket_created_at[SocketGroup(1, 0)] -= 2.0

        recovered = manager.recover_disconnected(min_age_seconds=0.0)

        assert recovered == 1
        assert original.closed is True
        assert len(BlockingFirstSocket.instances) == 2
        assert manager.snapshot()["socket_groups"] == 1
        assert manager.snapshot()["connected_groups"] == ["1:0"]
    finally:
        BlockingFirstSocket.release_first_connect.set()
        manager.close()


class BlockingSubscribeSocket(FakeSocket):
    subscribe_attempts = 0
    release_first_subscribe = Event()

    def subscribe_groups(self, groups, mode=None):
        self.subscribe_calls.append((dict(groups), mode))
        type(self).subscribe_attempts += 1
        if type(self).subscribe_attempts == 1:
            type(self).release_first_subscribe.wait(timeout=5.0)


def test_blocking_subscription_call_is_bounded_and_socket_is_recovered():
    registry = InstrumentRegistry()
    first = descriptor("101")
    second = descriptor("102")
    registry.register_many([first, second])
    BlockingSubscribeSocket.subscribe_attempts = 0
    BlockingSubscribeSocket.release_first_subscribe.clear()
    manager = CommonWebSocketManager(registry, socket_factory=BlockingSubscribeSocket)
    manager._socket_call_wait_seconds = 0.05
    manager._recovery_interval_seconds = 60.0
    try:
        manager.subscribe("cash", [first.key])
        original = BlockingSubscribeSocket.instances[0]

        started = time.monotonic()
        try:
            manager.subscribe("cash", [second.key])
        except TimeoutError as exc:
            assert "subscribe call exceeded" in str(exc)
        else:
            raise AssertionError("expected blocked subscription call to time out")
        assert time.monotonic() - started < 1.0
        assert original.closed is True
        assert manager.snapshot()["socket_groups"] == 0

        manager.recover_disconnected(min_age_seconds=0.0)
        snapshot = manager.snapshot()
        assert snapshot["socket_groups"] == 1
        assert snapshot["connected_groups"] == ["1:0"]
        assert len(BlockingSubscribeSocket.instances) == 2
    finally:
        BlockingSubscribeSocket.release_first_subscribe.set()
        manager.close()
        BlockingSubscribeSocket.subscribe_attempts = 0


def test_angel_failure_callback_triggers_immediate_recovery_and_rejects_old_generation_ticks():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    manager._recovery_interval_seconds = 60.0
    seen = []
    manager.register_callback("cash", lambda message: seen.append(message["token"]))
    try:
        manager.subscribe("cash", [d.key])
        original = FakeSocket.instances[0]
        old_connect = original.connect_calls[0]
        old_data = old_connect["on_data"]
        old_generation = manager._socket_generation[SocketGroup(1, 0)]

        old_connect["on_failure"]("simulated Angel disconnect")

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and len(FakeSocket.instances) < 2:
            time.sleep(0.01)
        assert len(FakeSocket.instances) == 2
        replacement = FakeSocket.instances[1]
        new_connect = replacement.connect_calls[0]
        new_generation = manager._socket_generation[SocketGroup(1, 0)]
        assert new_generation > old_generation
        assert original.closed is True

        old_data({"token": "101", "exchange_type": 1})
        assert seen == []
        new_connect["on_data"]({"token": "101", "exchange_type": 1})
        assert seen == ["101"]
        snapshot = manager.snapshot()
        assert snapshot["socket_failure_events"] == 1
        assert snapshot["last_socket_failure"]["reason"] == "simulated Angel disconnect"
    finally:
        manager.close()


def test_recovery_rebuilds_missing_expected_group_when_socket_count_looks_complete():
    registry = InstrumentRegistry()
    first = descriptor("101")
    second = descriptor("102")
    registry.register_many([first, second])
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    manager._max_tokens_per_socket = 1
    manager._recovery_interval_seconds = 60.0
    try:
        manager.subscribe("cash", [first.key, second.key])
        expected = {SocketGroup(1, 0), SocketGroup(1, 1)}
        assert set(manager._sockets) == expected

        # Simulate an unexpected stale group masking the missing shard while
        # leaving the total socket count equal to the expected count.
        with manager._lock:
            socket = manager._sockets.pop(SocketGroup(1, 1))
            manager._sockets[SocketGroup(1, 9)] = socket
            manager._socket_tokens[SocketGroup(1, 9)] = manager._socket_tokens.pop(SocketGroup(1, 1))
            manager._socket_created_at[SocketGroup(1, 9)] = manager._socket_created_at.pop(SocketGroup(1, 1))
            manager._last_data_at[SocketGroup(1, 9)] = manager._last_data_at.pop(SocketGroup(1, 1))
            manager._socket_generation[SocketGroup(1, 9)] = manager._socket_generation.pop(SocketGroup(1, 1))

        assert len(manager._sockets) == len(expected)
        manager.recover_disconnected(min_age_seconds=0.0)

        assert set(manager._sockets) == expected
        assert SocketGroup(1, 9) not in manager._sockets
        assert manager.snapshot()["socket_groups"] == 2
    finally:
        manager.close()


def test_common_manager_disables_nested_socket_reconnect_supervisor():
    manager = CommonWebSocketManager()
    socket = manager._create_socket()
    captured = {}
    group = SocketGroup(1, 0)
    socket.connect = lambda **kwargs: captured.update(kwargs)
    with manager._lock:
        manager._sockets[group] = socket
        manager._socket_tokens[group] = {(1, "101")}
        manager._socket_created_at[group] = time.monotonic()
        manager._last_data_at[group] = time.monotonic()
        manager._socket_generation[group] = 1
    try:
        assert isinstance(socket, MarketDataWebSocket)
        assert socket._auto_reconnect is False
        manager._start_socket_connect(
            socket=socket,
            group=group,
            pairs={(1, "101")},
            subscriptions={1: ["101"]},
            generation=1,
        )
        assert captured["reconnect_attempts"] == 0
        assert captured["reconnect_delay_seconds"] == 0.0
    finally:
        manager.close()


class LateSuccessfulConnectSocket(FakeSocket):
    attempts = 0
    release_first_connect = Event()
    late_connect_finished = Event()

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        type(self).attempts += 1
        if type(self).attempts == 1:
            self.connecting = True
            type(self).release_first_connect.wait(timeout=5.0)
            super().connect(**kwargs)
            type(self).late_connect_finished.set()
            return
        super().connect(**kwargs)


def test_late_successful_connect_is_closed_after_recovery_replaces_socket():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    LateSuccessfulConnectSocket.attempts = 0
    LateSuccessfulConnectSocket.release_first_connect.clear()
    LateSuccessfulConnectSocket.late_connect_finished.clear()
    manager = CommonWebSocketManager(registry, socket_factory=LateSuccessfulConnectSocket)
    manager._connect_call_wait_seconds = 0.05
    manager._connect_timeout_seconds = 1.0
    manager._recovery_interval_seconds = 60.0
    try:
        manager.subscribe("cash", [d.key])
        original = LateSuccessfulConnectSocket.instances[0]
        with manager._lock:
            manager._socket_created_at[SocketGroup(1, 0)] -= 2.0
        assert manager.recover_disconnected(min_age_seconds=0.0) == 1
        replacement = manager._sockets[SocketGroup(1, 0)]
        assert replacement is not original

        LateSuccessfulConnectSocket.release_first_connect.set()
        assert LateSuccessfulConnectSocket.late_connect_finished.wait(timeout=1.0)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and original.connected:
            time.sleep(0.01)
        assert original.closed is True
        assert original.connected is False
        assert replacement.connected is True
        assert len([item for item in LateSuccessfulConnectSocket.instances if not item.closed]) == 1
    finally:
        LateSuccessfulConnectSocket.release_first_connect.set()
        manager.close()
        LateSuccessfulConnectSocket.attempts = 0


def test_failure_callback_invalidation_waits_for_reconcile_snapshot_to_finish():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    manager._recovery_interval_seconds = 60.0
    try:
        manager.subscribe("cash", [d.key])
        original = FakeSocket.instances[0]
        callback = original.connect_calls[0]["on_failure"]
        callback_finished = Event()
        with manager._reconcile_lock:
            worker = Thread(
                target=lambda: (callback("concurrent failure"), callback_finished.set()),
                daemon=True,
            )
            worker.start()
            time.sleep(0.05)
            assert not callback_finished.is_set()
            assert manager._sockets[SocketGroup(1, 0)] is original

        assert callback_finished.wait(timeout=1.0)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and len(FakeSocket.instances) < 2:
            time.sleep(0.01)
        assert len(FakeSocket.instances) == 2
        assert manager._sockets[SocketGroup(1, 0)] is FakeSocket.instances[1]
        assert original.closed is True
    finally:
        manager.close()



class GuardedSocket(FakeSocket):
    forbidden_socket = None
    created_while_forbidden_open = False

    def __init__(self):
        if self.forbidden_socket is not None and not self.forbidden_socket.closed:
            type(self).created_while_forbidden_open = True
        super().__init__()


def test_recovery_closes_unexpected_group_before_opening_missing_shard():
    registry = InstrumentRegistry()
    descriptors = [descriptor(str(i)) for i in range(3)]
    registry.register_many(descriptors)
    GuardedSocket.forbidden_socket = None
    GuardedSocket.created_while_forbidden_open = False
    manager = CommonWebSocketManager(registry, socket_factory=GuardedSocket)
    manager._max_tokens_per_socket = 1
    manager._recovery_interval_seconds = 60.0
    try:
        manager.subscribe("cash", [item.key for item in descriptors])
        expected = {SocketGroup(1, 0), SocketGroup(1, 1), SocketGroup(1, 2)}
        assert set(manager._sockets) == expected
        unexpected_socket = manager._sockets.pop(SocketGroup(1, 2))
        manager._sockets[SocketGroup(1, 9)] = unexpected_socket
        manager._socket_tokens[SocketGroup(1, 9)] = manager._socket_tokens.pop(SocketGroup(1, 2))
        manager._socket_created_at[SocketGroup(1, 9)] = manager._socket_created_at.pop(SocketGroup(1, 2))
        manager._last_data_at[SocketGroup(1, 9)] = manager._last_data_at.pop(SocketGroup(1, 2))
        manager._socket_generation[SocketGroup(1, 9)] = manager._socket_generation.pop(SocketGroup(1, 2))
        GuardedSocket.forbidden_socket = unexpected_socket

        manager.recover_disconnected(min_age_seconds=0.0)

        assert unexpected_socket.closed is True
        assert GuardedSocket.created_while_forbidden_open is False
        assert set(manager._sockets) == expected
        assert len([s for s in GuardedSocket.instances if not s.closed]) == 3
    finally:
        manager.close()
        GuardedSocket.forbidden_socket = None



def test_closed_manager_does_not_reopen_sockets_from_late_recovery():
    registry = InstrumentRegistry()
    d = descriptor("101")
    registry.register(d)
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    manager._recovery_interval_seconds = 60.0
    manager.subscribe("cash", [d.key])
    assert len(FakeSocket.instances) == 1

    manager.close()
    assert manager.snapshot()["socket_groups"] == 0
    assert manager.recover_disconnected(min_age_seconds=0.0) == 0
    time.sleep(0.05)
    assert manager.snapshot()["socket_groups"] == 0
    assert len([item for item in FakeSocket.instances if not item.closed]) == 0

    try:
        manager.subscribe("cash", [d.key])
    except RuntimeError as exc:
        assert "closed" in str(exc)
    else:
        raise AssertionError("closed manager must reject new subscriptions")
