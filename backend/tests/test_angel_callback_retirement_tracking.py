from threading import Event, Thread
import time

from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import Subscription


def _valid_subscription():
    return Subscription(
        key=InstrumentKey(exchange="NSE", segment="NSE", token="test-token"),
        consumers=frozenset({"test-consumer"}),
        mode=1,
    )


class TimedOutAdapter:
    def __init__(self, worker):
        self._thread = worker

    def stop(self, *, timeout, drain):
        raise TimeoutError("consumer callback still running")


def test_retired_callback_worker_remains_visible_until_worker_exits():
    release = Event()
    worker = Thread(target=lambda: release.wait(timeout=2.0), daemon=True)
    worker.start()
    manager = CommonWebSocketManager()
    manager._stop_retired_callback_adapter(TimedOutAdapter(worker), SocketGroup(1, 0), 7)

    state = manager.snapshot()
    assert state["retired_callback_worker_count"] == 1
    retired = state["retired_callback_workers"][0]
    assert retired["group"] == "1:0"
    assert retired["generation"] == 7
    assert retired["worker_alive"] is True

    release.set()
    worker.join(timeout=1.0)
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["retired_callback_worker_count"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.snapshot()["retired_callback_worker_count"] == 0
    manager.close()


class BlockingSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_timed_out_socket_operation_is_tracked_until_late_completion():
    release = Event()
    socket = BlockingSocket()
    manager = CommonWebSocketManager()
    group = SocketGroup(1, 0)
    manager._socket_call_wait_seconds = 0.02
    manager._sockets[group] = socket
    manager._socket_generation_counter[group] = 4
    manager._close_socket_tracked = lambda *args, **kwargs: Thread()  # avoid close worker in this test



    def blocked_operation():
        release.wait(timeout=2.0)



    try:
        manager._run_socket_operation_bounded(
            socket=socket, group=group, operation=blocked_operation, operation_name="subscribe"
        )
    except TimeoutError:
        pass
    else:
        raise AssertionError("blocking operation should time out")



    snapshot = manager.snapshot()
    assert snapshot["socket_operation_worker_count"] == 1
    assert snapshot["socket_operation_worker_timeouts"] == 1
    assert snapshot["socket_operation_workers"][0]["timed_out"] is True
    assert group not in manager._sockets



    release.set()
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["socket_operation_worker_count"] and time.monotonic() < deadline:
        time.sleep(0.005)
    after = manager.snapshot()
    assert after["socket_operation_worker_count"] == 0
    assert after["socket_operation_worker_late_completions"] == 1
    manager.close()



def test_socket_operation_worker_tracking_clears_on_success_and_failure():
    manager = CommonWebSocketManager()
    group = SocketGroup(1, 0)
    socket = BlockingSocket()
    manager._socket_call_wait_seconds = 0.2
    manager._sockets[group] = socket
    manager._close_socket_tracked = lambda *args, **kwargs: Thread()
    manager._run_socket_operation_bounded(
        socket=socket, group=group, operation=lambda: None, operation_name="subscribe"
    )
    assert manager.snapshot()["socket_operation_worker_count"] == 0

    manager._sockets[group] = socket
    try:
        manager._run_socket_operation_bounded(
            socket=socket, group=group, operation=lambda: (_ for _ in ()).throw(RuntimeError("SDK failure")), operation_name="unsubscribe"
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("failed SDK operation should propagate")
    assert manager.snapshot()["socket_operation_worker_count"] == 0
    assert manager.snapshot()["socket_operation_worker_failures"] == 1
    manager.close()


def test_second_broker_operation_for_group_rejected_while_late_worker_is_active():
    release = Event()
    manager = CommonWebSocketManager()
    group = SocketGroup(1, 0)
    manager._socket_call_wait_seconds = 0.01
    socket = BlockingSocket()
    manager._sockets[group] = socket
    manager._close_socket_tracked = lambda *args, **kwargs: Thread()
    try:
        manager._run_socket_operation_bounded(
            socket=socket, group=group, operation=lambda: release.wait(timeout=2.0), operation_name="subscribe"
        )
    except TimeoutError:
        pass
    else:
        raise AssertionError("first blocking operation should time out")
    try:
        manager._run_socket_operation_bounded(
            socket=BlockingSocket(), group=group, operation=lambda: None, operation_name="unsubscribe"
        )
    except RuntimeError as exc:
        assert "still in flight" in str(exc)
    else:
        raise AssertionError("duplicate group operation must be rejected")
    assert manager.snapshot()["socket_operation_worker_admission_rejections"] == 1
    release.set()
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["socket_operation_worker_count"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert manager.snapshot()["socket_operation_worker_count"] == 0
    manager.close()



def test_manager_shutdown_blocks_reconcile_and_recovery_after_close():
    manager = CommonWebSocketManager()
    manager.close()

    try:
        manager._reconcile()
    except RuntimeError as exc:
        assert "closed" in str(exc)
    else:
        raise AssertionError("reconcile must reject after shutdown")


    assert manager.recover_disconnected(min_age_seconds=0.0) == 0
    manager._ensure_recovery_supervisor()
    assert manager._recovery_thread is None



def test_recovery_supervisor_exits_on_shutdown_without_retrying():
    manager = CommonWebSocketManager()
    manager._recovery_interval_seconds = 0.01
    recovery_calls = []
    recovery_called = Event()

    def recover_disconnected(**kwargs):
        recovery_calls.append(kwargs)
        recovery_called.set()
        return 0

    manager.recover_disconnected = recover_disconnected
    manager.registry.subscriptions = lambda: [_valid_subscription()]
    manager._ensure_recovery_supervisor()
    thread = manager._recovery_thread
    assert thread is not None and thread.is_alive()
    assert recovery_called.wait(timeout=1.0)
    assert recovery_calls


    manager.close()
    assert not thread.is_alive()
    calls_at_close = len(recovery_calls)
    time.sleep(0.03)
    assert len(recovery_calls) == calls_at_close



def test_recovery_supervisor_uses_bounded_backoff_after_transient_errors():
    manager = CommonWebSocketManager()
    manager._recovery_interval_seconds = 0.01
    manager._recovery_backoff_max_seconds = 0.04
    calls = []
    recovery_succeeded = Event()


    def failing_then_success(**kwargs):
        calls.append(time.monotonic())
        if len(calls) <= 2:
            raise RuntimeError("temporary broker outage")
        recovery_succeeded.set()
        return 0


    manager.recover_disconnected = failing_then_success
    manager.registry.subscriptions = lambda: [_valid_subscription()]
    manager._ensure_recovery_supervisor()
    assert recovery_succeeded.wait(timeout=1.0)
    state = manager.snapshot()
    assert state["recovery_backoff_errors"] == 2
    # The recovery loop enforces a 100ms minimum to avoid busy-loop retries.
    assert state["recovery_backoff_current_seconds"] == max(
        0.1, manager._recovery_interval_seconds
    )
    assert state["recovery_backoff_resets"] >= 1
    assert state["recovery_backoff_max_seconds"] == 0.04
    manager.close()



def test_simultaneous_shard_failures_coalesce_callback_recovery_workers():
    release = Event()
    started = Event()
    manager = CommonWebSocketManager()
    groups = [SocketGroup(1, 0), SocketGroup(1, 1)]
    sockets = [BlockingSocket(), BlockingSocket()]
    for group, socket in zip(groups, sockets):
        manager._sockets[group] = socket
        manager._socket_generation[group] = 1
        manager._socket_generation_counter[group] = 1
        manager._socket_tokens[group] = {(1, str(group.shard))}
    manager._close_socket_tracked = lambda *args, **kwargs: Thread()  # dead worker: close completed


    def wait_recovery(**kwargs):
        started.set()
        release.wait(timeout=1.0)
        return 0


    manager.recover_disconnected = wait_recovery
    manager._reconcile = lambda: None
    manager._schedule_socket_failure_recovery(groups[0], sockets[0], 1, "test failure")
    assert started.wait(timeout=1.0)
    manager._schedule_socket_failure_recovery(groups[1], sockets[1], 1, "test failure")
    state = manager.snapshot()
    assert state["failure_recovery_worker_active"] is True
    assert state["failure_recovery_admission_rejections"] == 1
    assert state["failure_recovery_pending_groups"] == ["1:1"]
    release.set()
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["failure_recovery_worker_active"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.snapshot()["failure_recovery_worker_active"] is False
    manager.close()
