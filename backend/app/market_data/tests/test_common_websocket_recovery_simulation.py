"""Fault-injection simulation tests for shared Angel WebSocket recovery.

These tests exercise the real CommonWebSocketManager state machine with a
deterministic fake broker socket. They do not open a real Angel connection or
place orders.
"""
from __future__ import annotations

import time
from threading import Event

from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry


class SimulatedAngelSocket:
    """Small deterministic broker double that retains SDK callbacks."""

    instances: list["SimulatedAngelSocket"] = []

    def __init__(self):
        self.connect_calls: list[dict] = []
        self.closed = False
        self.connected = False
        self.connecting = False
        type(self).instances.append(self)

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        self.connecting = False
        self.connected = True

    def subscribe(self, tokens, mode=None):
        pass

    def subscribe_groups(self, groups, mode=None):
        pass

    def unsubscribe_groups(self, groups):
        pass

    def close(self):
        self.closed = True
        self.connected = False


def make_descriptor(token: str = "101") -> InstrumentDescriptor:
    key = InstrumentKey("NSE", "EQ", token)
    return InstrumentDescriptor(
        key=key,
        symbol=f"SIM{token}",
        instrument_type="equity",
        exchange="NSE",
        segment="EQ",
    )


def wait_until(predicate, timeout: float = 1.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def setup_function():
    SimulatedAngelSocket.instances.clear()


def test_failure_callback_recreates_socket_and_resubscribes_then_delivers_ticks():
    registry = InstrumentRegistry()
    descriptor = make_descriptor()
    registry.register(descriptor)
    manager = CommonWebSocketManager(registry, socket_factory=SimulatedAngelSocket)
    manager._recovery_interval_seconds = 60.0
    delivered: list[dict] = []
    manager.register_callback("cash-future", delivered.append)

    try:
        manager.subscribe("cash-future", [descriptor.key])
        group = SocketGroup(1, 0)
        old_socket = manager._sockets[group]
        old_failure = old_socket.connect_calls[0]["on_failure"]
        old_data = old_socket.connect_calls[0]["on_data"]

        old_failure("simulated network disconnect")

        assert wait_until(
            lambda: group in manager._sockets
            and manager._sockets[group] is not old_socket
            and manager._sockets[group].connected
        ), "failure callback did not trigger socket recovery"

        replacement = manager._sockets[group]
        assert replacement.connect_calls, "replacement socket was not connected"
        assert replacement.connect_calls[0]["subscriptions"] == {1: ["101"]}
        assert manager.snapshot()["connected_groups"] == ["1:0"]

        # The old generation must not deliver late ticks after replacement.
        old_data({"token": "101", "ltp": 99.0})
        assert delivered == []

        # A tick from the new socket must flow through the real manager path.
        replacement.connect_calls[0]["on_data"]({"token": "101", "ltp": 100.0})
        assert wait_until(lambda: len(delivered) == 1)
        assert delivered[0]["token"] == "101"
    finally:
        manager.close()


def test_repeated_disconnects_recover_without_accumulating_live_sockets():
    registry = InstrumentRegistry()
    descriptor = make_descriptor()
    registry.register(descriptor)
    manager = CommonWebSocketManager(registry, socket_factory=SimulatedAngelSocket)
    manager._recovery_interval_seconds = 60.0

    try:
        manager.subscribe("cash-future", [descriptor.key])
        group = SocketGroup(1, 0)

        for attempt in range(3):
            current = manager._sockets[group]
            current.connect_calls[0]["on_failure"](f"simulated disconnect {attempt + 1}")
            assert wait_until(
                lambda current=current: group in manager._sockets
                and manager._sockets[group] is not current
                and manager._sockets[group].connected
            ), f"recovery attempt {attempt + 1} did not install a connected replacement"

            live_sockets = [socket for socket in SimulatedAngelSocket.instances if not socket.closed]
            assert live_sockets == [manager._sockets[group]], (
                "repeated recovery left an orphan/duplicate live socket"
            )

        assert manager.snapshot()["connected_groups"] == ["1:0"]
        assert len(SimulatedAngelSocket.instances) == 4
    finally:
        manager.close()


def test_close_during_simulation_is_terminal_and_late_failure_cannot_reopen():
    registry = InstrumentRegistry()
    descriptor = make_descriptor()
    registry.register(descriptor)
    manager = CommonWebSocketManager(registry, socket_factory=SimulatedAngelSocket)
    manager._recovery_interval_seconds = 60.0
    manager.subscribe("cash-future", [descriptor.key])

    old_socket = manager._sockets[SocketGroup(1, 0)]
    late_failure = old_socket.connect_calls[0]["on_failure"]
    manager.close()
    count_after_close = len(SimulatedAngelSocket.instances)

    late_failure("late callback after close")
    time.sleep(0.05)

    assert manager.snapshot()["socket_groups"] == 0
    assert len(SimulatedAngelSocket.instances) == count_after_close
    assert all(socket.closed for socket in SimulatedAngelSocket.instances)


class HangingConnectSocket(SimulatedAngelSocket):
    """Connect remains blocked until the test releases the old handshake."""

    def __init__(self):
        super().__init__()
        self.connect_started = Event()
        self.release_connect = Event()

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        # Only the first generation wedges; recovery's replacement is healthy.
        if len(type(self).instances) == 1:
            self.connecting = True
            self.connect_started.set()
            self.release_connect.wait(timeout=3.0)
        self.connecting = False
        self.connected = True


def test_late_connect_completion_is_closed_after_recovery_replaces_socket():
    registry = InstrumentRegistry()
    descriptor = make_descriptor()
    registry.register(descriptor)
    manager = CommonWebSocketManager(registry, socket_factory=HangingConnectSocket)
    manager._recovery_interval_seconds = 60.0
    manager._connect_call_wait_seconds = 0.02
    manager._connect_timeout_seconds = 1.0

    try:
        manager.subscribe("cash-future", [descriptor.key])
        group = SocketGroup(1, 0)
        old_socket = manager._sockets[group]
        assert old_socket.connect_started.wait(timeout=1.0), "connect worker did not start"

        # Deterministically simulate a handshake that has exceeded its deadline.
        manager._socket_created_at[group] = time.monotonic() - 2.0
        recovered = manager.recover_disconnected(min_age_seconds=0.0, silent_age_seconds=0.0)
        assert recovered == 1
        replacement = manager._sockets[group]
        assert replacement is not old_socket
        assert replacement.connected
        assert old_socket.closed

        # The old SDK call now returns successfully, but must not survive as an orphan.
        old_socket.release_connect.set()
        assert wait_until(lambda: old_socket.closed and not old_socket.connected)
        assert manager._sockets[group] is replacement
        live_sockets = [socket for socket in HangingConnectSocket.instances if not socket.closed]
        assert live_sockets == [replacement]
    finally:
        for socket in HangingConnectSocket.instances:
            socket.release_connect.set()
        manager.close()



def test_supervisor_replaces_hung_connect_without_manual_recovery():
    registry = InstrumentRegistry()
    descriptor = make_descriptor()
    registry.register(descriptor)
    manager = CommonWebSocketManager(registry, socket_factory=HangingConnectSocket)
    manager._recovery_interval_seconds = 0.02
    manager._connect_call_wait_seconds = 0.02
    manager._connect_timeout_seconds = 1.0

    try:
        manager.subscribe("cash-future", [descriptor.key])
        group = SocketGroup(1, 0)
        old_socket = manager._sockets[group]
        assert old_socket.connect_started.wait(timeout=1.0), "connect worker did not start"

        # Do not age internal timestamps or invoke recovery directly: the
        # periodic supervisor must detect the stuck handshake by itself.
        assert wait_until(
            lambda: (
                group in manager._sockets
                and manager._sockets[group] is not old_socket
                and manager._sockets[group].connected
            ),
            timeout=2.5,
        ), "automatic supervisor did not replace the hung connect"

        replacement = manager._sockets[group]
        assert old_socket.closed
        assert manager.snapshot()["subscriptions"] == 1
        assert manager.snapshot()["connected_groups"] == ["1:0"]
        live_sockets = [
            socket for socket in HangingConnectSocket.instances if not socket.closed
        ]
        assert live_sockets == [replacement]
    finally:
        for socket in HangingConnectSocket.instances:
            socket.release_connect.set()
        manager.close()


class SubscribeFailureSocket(SimulatedAngelSocket):
    fail_next_subscribe = False

    def subscribe_groups(self, groups, mode=None):
        if type(self).fail_next_subscribe:
            type(self).fail_next_subscribe = False
            raise RuntimeError("simulated subscription failure")


def test_subscription_failure_invalidates_socket_and_recovery_restores_full_intent():
    registry = InstrumentRegistry()
    first = make_descriptor("101")
    second = make_descriptor("102")
    registry.register(first)
    registry.register(second)
    manager = CommonWebSocketManager(registry, socket_factory=SubscribeFailureSocket)
    manager._recovery_interval_seconds = 60.0

    try:
        manager.subscribe("cash-future", [first.key])
        group = SocketGroup(1, 0)
        old_socket = manager._sockets[group]
        SubscribeFailureSocket.fail_next_subscribe = True

        try:
            manager.subscribe("cash-future", [second.key])
        except RuntimeError as exc:
            assert "simulated subscription failure" in str(exc)
        else:
            raise AssertionError("subscribe failure should propagate to caller")

        assert old_socket.closed
        assert group not in manager._sockets

        # Subscription intent remains in the registry; the next recovery rebuilds
        # the group with both tokens instead of silently losing the added token.
        manager.recover_disconnected(min_age_seconds=0.0, silent_age_seconds=0.0)
        replacement = manager._sockets[group]
        assert replacement is not old_socket
        assert replacement.connected
        assert replacement.connect_calls[0]["subscriptions"] == {1: ["101", "102"]}
    finally:
        manager.close()


class RepeatedConnectFailureSocket(SimulatedAngelSocket):
    """Fail the first two handshakes, then allow the supervisor retry to connect."""

    attempts = 0

    def connect(self, **kwargs):
        type(self).attempts += 1
        self.connect_calls.append(kwargs)
        if type(self).attempts <= 2:
            raise ConnectionError(f"simulated connect failure {type(self).attempts}")
        self.connecting = False
        self.connected = True


def test_supervisor_retries_repeated_connect_failures_until_socket_connects():
    registry = InstrumentRegistry()
    descriptor = make_descriptor()
    registry.register(descriptor)
    RepeatedConnectFailureSocket.attempts = 0
    manager = CommonWebSocketManager(
        registry, socket_factory=RepeatedConnectFailureSocket
    )
    manager._recovery_interval_seconds = 0.02

    try:
        try:
            manager.subscribe("cash-future", [descriptor.key])
        except ConnectionError as exc:
            assert "simulated connect failure 1" in str(exc)
        else:
            raise AssertionError("first simulated connect attempt should fail")

        # Do not call recover_disconnected() manually: the supervisor itself
        # must retry while the registry still retains subscription intent.
        assert wait_until(
            lambda: (
                manager.snapshot()["connected_groups"] == ["1:0"]
                and RepeatedConnectFailureSocket.attempts >= 3
            ),
            timeout=2.0,
        ), "automatic supervisor did not recover after repeated connect failures"

        snapshot = manager.snapshot()
        assert snapshot["subscriptions"] == 1
        assert snapshot["active_instruments"] == 1
        assert snapshot["connected_groups"] == ["1:0"]
        assert snapshot["connect_failures"] >= 2
        live_sockets = [
            socket for socket in RepeatedConnectFailureSocket.instances
            if not socket.closed
        ]
        assert live_sockets == [manager._sockets[SocketGroup(1, 0)]]
    finally:
        manager.close()
        RepeatedConnectFailureSocket.attempts = 0


def test_supervisor_replaces_connected_but_silent_socket_automatically():
    registry = InstrumentRegistry()
    descriptor = make_descriptor()
    registry.register(descriptor)
    manager = CommonWebSocketManager(registry, socket_factory=SimulatedAngelSocket)
    manager._recovery_interval_seconds = 0.02
    manager._silent_feed_timeout_seconds = 0.05

    try:
        manager.subscribe("cash-future", [descriptor.key])
        group = SocketGroup(1, 0)
        old_socket = manager._sockets[group]
        assert old_socket.connected

        # The transport still reports connected, but no matching tick arrives.
        # The watchdog must recover without manually calling recover_disconnected.
        assert wait_until(
            lambda: (
                group in manager._sockets
                and manager._sockets[group] is not old_socket
                and manager._sockets[group].connected
            ),
            timeout=1.5,
        ), "supervisor did not replace a connected-but-silent socket"

        replacement = manager._sockets[group]
        assert old_socket.closed
        assert replacement.connect_calls[0]["subscriptions"] == {1: ["101"]}
        snapshot = manager.snapshot()
        assert snapshot["subscriptions"] == 1
        assert snapshot["connected_groups"] == ["1:0"]
        live_sockets = [
            socket for socket in SimulatedAngelSocket.instances if not socket.closed
        ]
        assert live_sockets == [replacement]
    finally:
        manager.close()



class ConnectReturnsWithoutConnectingSocket(SimulatedAngelSocket):
    """The SDK call returns, but the first socket never reaches connected state."""

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        self.connecting = False
        if len(type(self).instances) == 1:
            self.connected = False
            return
        self.connected = True


def test_supervisor_retries_socket_when_connect_returns_without_connected_state():
    registry = InstrumentRegistry()
    descriptor = make_descriptor()
    registry.register(descriptor)
    manager = CommonWebSocketManager(
        registry, socket_factory=ConnectReturnsWithoutConnectingSocket
    )
    manager._recovery_interval_seconds = 0.02

    try:
        manager.subscribe("cash-future", [descriptor.key])
        group = SocketGroup(1, 0)
        failed_socket = manager._sockets[group]
        assert failed_socket.connect_calls
        assert not failed_socket.connected
        assert not failed_socket.connecting

        # No error callback is fired: recovery must infer failure from state/age.
        assert wait_until(
            lambda: group in manager._sockets
            and manager._sockets[group] is not failed_socket
            and manager._sockets[group].connected,
            timeout=2.5,
        ), "supervisor did not replace a connect call that returned disconnected"

        replacement = manager._sockets[group]
        assert replacement.connect_calls[0]["subscriptions"] == {1: ["101"]}
        assert failed_socket.closed
        assert manager.snapshot()["connected_groups"] == ["1:0"]
        live_sockets = [
            socket for socket in ConnectReturnsWithoutConnectingSocket.instances
            if not socket.closed
        ]
        assert live_sockets == [replacement]
    finally:
        manager.close()
