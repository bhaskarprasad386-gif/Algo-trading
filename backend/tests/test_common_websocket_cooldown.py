from __future__ import annotations

import time

from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor


def _manager_with_subscription() -> CommonWebSocketManager:
    manager = CommonWebSocketManager()
    key = InstrumentKey(exchange="NSE", segment="CASH", token="123")
    manager.registry.register(
        InstrumentDescriptor(
            key=key,
            symbol="TEST",
            instrument_type="equity",
            exchange="NSE",
            segment="CASH",
        )
    )
    manager.registry.subscribe("cooldown-test", key, mode=1)
    return manager


def test_429_starts_broker_cooldown() -> None:
    manager = CommonWebSocketManager()
    manager._apply_broker_cooldown("Handshake status 429: Connection Limit Exceeded")
    assert manager._broker_cooldown_active()


def test_unrelated_failure_does_not_start_cooldown() -> None:
    manager = CommonWebSocketManager()
    manager._apply_broker_cooldown("temporary DNS resolution failure")
    assert not manager._broker_cooldown_active()


def test_reconcile_does_not_open_socket_during_cooldown() -> None:
    manager = _manager_with_subscription()
    manager._broker_cooldown_until = time.monotonic() + 30.0

    def unexpected_socket_creation():
        raise AssertionError("socket creation attempted during broker cooldown")

    manager._create_socket = unexpected_socket_creation
    manager._reconcile()


def test_recovery_is_blocked_during_cooldown() -> None:
    manager = CommonWebSocketManager()
    manager._broker_cooldown_until = time.monotonic() + 30.0
    manager._recover_disconnected_serialized = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("recovery executed during broker cooldown")
    )
    assert manager.recover_disconnected(min_age_seconds=0.0) == 0



def test_pending_connect_worker_skips_broker_call_during_cooldown() -> None:
    from app.market_data.common_websocket import SocketGroup

    manager = CommonWebSocketManager()
    group = SocketGroup(mode=1, shard=0)

    class Socket:
        connect_calls = 0
        closed = False

        def connect(self, **kwargs) -> None:
            self.connect_calls += 1
            raise AssertionError("broker connect must not run during cooldown")

        def close(self) -> None:
            self.closed = True

    socket = Socket()
    manager._sockets[group] = socket
    manager._socket_generation[group] = 1
    manager._broker_cooldown_until = time.monotonic() + 30.0

    manager._start_socket_connect(
        socket=socket,
        group=group,
        pairs={(1, "123")},
        subscriptions={1: ["123"]},
        generation=1,
    )

    assert socket.connect_calls == 0
    assert socket.closed
    assert group not in manager._sockets
