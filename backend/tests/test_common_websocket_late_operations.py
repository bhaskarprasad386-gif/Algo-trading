from threading import Event
from time import monotonic, sleep

import pytest

import app.market_data.common_websocket as common_websocket_module
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
    manager._socket_call_wait_seconds = 0.15

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
    with manager._lock:
        assert manager._has_pending_socket_operation_locked(group)
        assert not manager._has_pending_socket_operation_locked(SocketGroup(mode=1, shard=1))

    release_operation.set()
    assert reconciled.wait(1.0), "late worker completion should promptly trigger reconciliation"
    deadline = monotonic() + 1.0
    while manager.snapshot()["socket_operation_worker_count"] and monotonic() < deadline:
        sleep(0.01)
    assert manager.snapshot()["socket_operation_worker_count"] == 0
    with manager._lock:
        assert not manager._has_pending_socket_operation_locked(group)
    assert calls
    manager.close()


def test_timeout_wait_completion_race_still_reconciles(monkeypatch):
    manager = CommonWebSocketManager(socket_factory=lambda: _Socket())
    group = SocketGroup(mode=1, shard=0)
    socket = _Socket()
    with manager._lock:
        manager._sockets[group] = socket
        manager._socket_tokens[group] = {(1, "123")}
        manager._socket_generation[group] = 2
        manager._socket_generation_counter[group] = 2

    release_operation = Event()
    reconcile_called = Event()

    class _CompletionEvent:
        def __init__(self):
            self._event = Event()

        def set(self):
            self._event.set()

        def is_set(self):
            return self._event.is_set()

        def wait(self, timeout=None):
            # Force the edge case: the worker finishes before the caller
            # marks its tracked operation as timed out, but wait reports
            # that the bounded wait expired.
            release_operation.set()
            assert self._event.wait(1.0)
            return False

    monkeypatch.setattr(common_websocket_module, "Event", _CompletionEvent)
    manager._reconcile = reconcile_called.set

    with pytest.raises(TimeoutError, match="exceeded"):
        manager._run_socket_operation_bounded(
            socket=socket,
            group=group,
            operation=lambda: release_operation.wait(1.0),
            operation_name="subscribe",
        )

    assert reconcile_called.is_set(), "timeout/completion race must not miss reconcile"
    assert manager.snapshot()["socket_operation_worker_count"] == 0
    manager.close()


def test_late_operation_completion_after_shutdown_does_not_reconcile():
    manager = CommonWebSocketManager(socket_factory=lambda: _Socket())
    group = SocketGroup(mode=1, shard=0)
    socket = _Socket()
    with manager._lock:
        manager._sockets[group] = socket
        manager._socket_tokens[group] = {(1, "123")}
        manager._socket_generation[group] = 3
        manager._socket_generation_counter[group] = 3


    operation_entered = Event()
    release_operation = Event()
    reconciled = Event()


    def late_operation():
        operation_entered.set()
        assert release_operation.wait(2.0)


    manager._reconcile = reconciled.set
    manager._socket_call_wait_seconds = 0.1
    with pytest.raises(TimeoutError, match="exceeded"):
        manager._run_socket_operation_bounded(
            socket=socket,
            group=group,
            operation=late_operation,
            operation_name="unsubscribe",
        )
    assert operation_entered.is_set()
    assert manager.snapshot()["socket_operation_worker_count"] == 1


    manager.close()
    release_operation.set()
    deadline = monotonic() + 1.0
    while manager.snapshot()["socket_operation_worker_count"] and monotonic() < deadline:
        sleep(0.01)
    assert manager.snapshot()["socket_operation_worker_count"] == 0
    assert not reconciled.is_set(), "shutdown is terminal; late SDK completion must not restart reconcile"


def test_late_operation_failure_after_shutdown_is_diagnostic_only():
    manager = CommonWebSocketManager(socket_factory=lambda: _Socket())
    group = SocketGroup(mode=1, shard=1)
    socket = _Socket()
    with manager._lock:
        manager._sockets[group] = socket
        manager._socket_tokens[group] = {(1, "456")}
        manager._socket_generation[group] = 5
        manager._socket_generation_counter[group] = 5

    entered = Event()
    release = Event()
    reconciled = Event()


    def late_failure():
        entered.set()
        assert release.wait(2.0)
        raise RuntimeError("late broker failure")


    manager._reconcile = reconciled.set
    manager._socket_call_wait_seconds = 0.1
    with pytest.raises(TimeoutError, match="exceeded"):
        manager._run_socket_operation_bounded(
            socket=socket,
            group=group,
            operation=late_failure,
            operation_name="subscribe",
        )
    assert entered.is_set()
    manager.close()
    release.set()
    deadline = monotonic() + 1.0
    while manager.snapshot()["socket_operation_worker_count"] and monotonic() < deadline:
        sleep(0.01)
    snapshot = manager.snapshot()
    assert snapshot["socket_operation_worker_count"] == 0
    assert snapshot["socket_operation_worker_late_completions"] >= 1
    assert snapshot["socket_operation_worker_late_failures"] >= 1
    assert not reconciled.is_set(), "late failure after shutdown must not trigger recovery"
