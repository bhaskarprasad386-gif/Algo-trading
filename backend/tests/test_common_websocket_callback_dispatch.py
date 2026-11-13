from threading import Event, get_ident
from time import monotonic, sleep

from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup


class _Socket:
    connected = True
    connecting = False

    def close(self):
        self.connected = False


def _install_current(manager, group, socket, generation=1):
    with manager._lock:
        manager._sockets[group] = socket
        manager._socket_generation[group] = generation
        manager._socket_generation_counter[group] = generation


def test_callback_dispatch_is_synchronous_by_default():
    manager = CommonWebSocketManager()
    group = SocketGroup(mode=1, shard=0)
    socket = _Socket()
    _install_current(manager, group, socket)
    seen = []
    manager._on_data = lambda got_group, payload, generation: seen.append(
        (got_group, payload, generation)
    )

    manager._dispatch_transport_data(group, socket, {"token": "123"}, 1)

    assert seen == [(group, {"token": "123"}, 1)]
    assert manager.snapshot()["async_callback_dispatch"] is False
    assert manager.snapshot()["callback_dispatch_by_group"] == {}
    manager.close()


def test_opt_in_callback_dispatch_runs_manager_delivery_on_shard_worker():
    manager = CommonWebSocketManager(async_callback_dispatch=True, callback_dispatch_capacity=8)
    group = SocketGroup(mode=1, shard=0)
    socket = _Socket()
    _install_current(manager, group, socket)
    delivered = Event()
    caller_thread = get_ident()
    seen = []

    def on_data(got_group, payload, generation):
        seen.append((got_group, payload, generation, get_ident()))
        delivered.set()

    manager._on_data = on_data
    manager._install_callback_adapter(group, 1)
    manager._dispatch_transport_data(group, socket, {"token": "123"}, 1)

    assert delivered.wait(1.0)
    assert seen[0][:3] == (group, {"token": "123"}, 1)
    assert seen[0][3] != caller_thread
    state = manager.snapshot()
    assert state["async_callback_dispatch"] is True
    assert state["callback_dispatch_by_group"]["1:0"]["processed_batches"] == 1
    manager.close()


def test_callback_queue_overflow_requests_only_current_shard_recovery_off_transport_thread():
    manager = CommonWebSocketManager(async_callback_dispatch=True, callback_dispatch_capacity=1)
    group = SocketGroup(mode=1, shard=0)
    socket = _Socket()
    _install_current(manager, group, socket)
    entered = Event()
    release = Event()
    recovery_called = Event()
    recovery_threads = []

    def blocked_on_data(_group, _payload, _generation):
        entered.set()
        release.wait(2.0)

    def record_recovery(_group, _socket, _generation, reason):
        recovery_threads.append((get_ident(), reason))
        recovery_called.set()

    manager._on_data = blocked_on_data
    manager._schedule_socket_failure_recovery = record_recovery
    manager._install_callback_adapter(group, 1)

    manager._dispatch_transport_data(group, socket, {"token": "inflight"}, 1)
    assert entered.wait(1.0)
    manager._dispatch_transport_data(group, socket, {"token": "queued"}, 1)
    transport_thread = get_ident()
    manager._dispatch_transport_data(group, socket, {"token": "overflow"}, 1)

    assert recovery_called.wait(1.0)
    assert recovery_threads[0][0] != transport_thread
    assert recovery_threads[0][1] == "callback_dispatch_integrity_fault"
    state = manager.snapshot()["callback_dispatch_by_group"]["1:0"]
    assert state["integrity_fault"] is True
    assert state["delivery_status"] == "DEGRADED"
    assert state["queue_depth"] == 0
    # Retired generation diagnostics remain visible after manager recovery
    # removes the active adapter from the current-generation map.
    manager._retire_callback_adapter_locked(group)
    retired = manager.snapshot()["callback_dispatch_retired_faults"]["1:0"]
    assert retired["generation"] == 1
    assert retired["integrity_fault"] is True

    release.set()
    manager.close()


def test_retiring_adapter_does_not_join_worker_while_manager_lock_is_held():
    manager = CommonWebSocketManager(async_callback_dispatch=True, callback_dispatch_capacity=2)
    group = SocketGroup(mode=1, shard=0)
    socket = _Socket()
    _install_current(manager, group, socket)
    manager._install_callback_adapter(group, 1)
    adapter = manager._callback_adapters[group][1]

    started = monotonic()
    with manager._lock:
        manager._retire_callback_adapter_locked(group)
    elapsed = monotonic() - started

    assert elapsed < 0.2
    assert group not in manager._callback_adapters
    deadline = monotonic() + 1.0
    while not adapter.snapshot()["closed"] and monotonic() < deadline:
        sleep(0.01)
    assert adapter.snapshot()["closed"] is True
    manager.close()

def test_callback_dispatch_configuration_rejects_truthy_non_boolean_values():
    import pytest

    with pytest.raises(TypeError, match="async_callback_dispatch must be a bool"):
        CommonWebSocketManager(async_callback_dispatch="false")
    with pytest.raises(ValueError, match="callback_dispatch_capacity"):
        CommonWebSocketManager(callback_dispatch_capacity=0)

def test_overflow_reaches_generation_checked_manager_recovery_boundary():
    manager = CommonWebSocketManager(async_callback_dispatch=True, callback_dispatch_capacity=1)
    group = SocketGroup(mode=1, shard=0)
    socket = _Socket()
    _install_current(manager, group, socket, generation=7)
    manager._install_callback_adapter(group, 7)
    requested = Event()
    observed = []

    def record_recovery(got_group, got_socket, generation, reason):
        observed.append((got_group, got_socket, generation, reason, get_ident()))
        requested.set()

    manager._schedule_socket_failure_recovery = record_recovery
    adapter = manager._callback_adapters[group][1]
    adapter._fail_closed_locked("test_overflow")
    manager._dispatch_transport_data(group, socket, {"token": "late"}, 7)

    assert requested.wait(1.0)
    assert observed[0][:4] == (
        group, socket, 7, "callback_dispatch_integrity_fault"
    )
    assert observed[0][4] != get_ident()
    assert manager._callback_adapters[group][1] is adapter
    assert adapter.snapshot()["integrity_fault"] is True
    manager.close()


def test_retired_generation_callback_cannot_be_dispatched_after_replacement():
    manager = CommonWebSocketManager(async_callback_dispatch=True, callback_dispatch_capacity=4)
    group = SocketGroup(mode=1, shard=0)
    old_socket = _Socket()
    new_socket = _Socket()
    _install_current(manager, group, old_socket, generation=1)
    manager._install_callback_adapter(group, 1)
    manager._retire_callback_adapter_locked(group)
    _install_current(manager, group, new_socket, generation=2)
    manager._install_callback_adapter(group, 2)
    delivered = Event()
    manager._on_data = lambda *_args: delivered.set()

    manager._dispatch_transport_data(group, old_socket, {"token": "old"}, 1)
    assert not delivered.wait(0.05)
    manager._dispatch_transport_data(group, new_socket, {"token": "new"}, 2)
    assert delivered.wait(1.0)
    manager.close()
