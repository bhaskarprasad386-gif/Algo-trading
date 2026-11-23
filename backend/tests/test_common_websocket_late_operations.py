from threading import Event
from time import monotonic, sleep

import pytest

from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup


class _Socket:
    connected = True
    connecting = False

    def close(self):
        self.connected = False


def test_late_broker_operation_completion_triggers_registry_reconcile():
    manager = CommonWebSocketManager(socket_factory=lambda: _Socket())
    group = SocketGroup(mode=1, shard=0)
    socket = _Socket()
    with manager._lock:
        manager._sockets[group] = socket
        manager._socket_tokens[group] = {(1, "123")}
        manager._socket_generation[group] = 4
        manager._socket_generation_counter[group] = 4

    operation_entered = Event()
    release_operation = Event()
    reconciled = Event()
    calls = []

    def late_operation():
        operation_entered.set()
        assert release_operation.wait(2.0)

    def reconcile():
        calls.append(monotonic())
        reconciled.set()

    manager._reconcile = reconcile
    manager._socket_call_wait_seconds = 0.02

    with pytest.raises(TimeoutError, match="exceeded"):
        manager._run_socket_operation_bounded(
            socket=socket,
            group=group,
            operation=late_operation,
            operation_name="subscribe",
        )

    assert operation_entered.is_set()
    assert group not in manager._sockets
    assert manager.snapshot()["socket_operation_worker_count"] == 1

    release_operation.set()
    assert reconciled.wait(1.0), "late worker completion should promptly trigger reconciliation"
    deadline = monotonic() + 1.0
    while manager.snapshot()["socket_operation_worker_count"] and monotonic() < deadline:
        sleep(0.01)
    assert manager.snapshot()["socket_operation_worker_count"] == 0
    assert calls
    manager.close()
