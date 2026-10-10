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
