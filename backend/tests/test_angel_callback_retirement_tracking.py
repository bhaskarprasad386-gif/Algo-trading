from threading import Event, Thread
import time

from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor


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



def test_shutdown_keeps_timed_out_socket_close_visible_until_late_exit():
    close_started = Event()
    release_close = Event()
    socket = BlockingSocket()

    def blocked_close():
        close_started.set()
        release_close.wait(timeout=3.0)
        socket.closed = True

    socket.close = blocked_close
    manager = CommonWebSocketManager()
    group = SocketGroup(1, 0)
    manager._sockets[group] = socket
    manager._socket_generation[group] = 2
    manager._socket_generation_counter[group] = 2

    shutdown = Thread(target=manager.close, daemon=True)
    shutdown.start()
    assert close_started.wait(timeout=1.0)
    state = manager.snapshot()
    assert state["retired_transport_count"] >= 1
    assert any(item["reason"] == "manager_shutdown" for item in state["retired_transports"])
    release_close.set()
    shutdown.join(timeout=3.0)
    assert not shutdown.is_alive()
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["retired_transport_count"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert manager.snapshot()["retired_transport_count"] == 0


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



def test_supervisor_drains_coalesced_failure_groups_after_successful_recovery():
    manager = CommonWebSocketManager()
    manager._recovery_interval_seconds = 0.01
    pending = {SocketGroup(1, 1), SocketGroup(1, 2)}
    manager._failure_recovery_pending_groups.update(pending)
    recovery_succeeded = Event()
    manager.registry.subscriptions = lambda: [object()]

    def recover(**kwargs):
        recovery_succeeded.set()
        return 0

    manager.recover_disconnected = recover
    manager._ensure_recovery_supervisor()
    assert recovery_succeeded.wait(timeout=1.0)
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["failure_recovery_pending_groups"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert manager.snapshot()["failure_recovery_pending_groups"] == []
    manager.close()


def test_recovery_supervisor_exits_on_shutdown_without_retrying():
    manager = CommonWebSocketManager()
    # Production retry cadence is clamped to 100ms to avoid tight loops.
    manager._recovery_interval_seconds = 0.1
    recovery_calls = []
    manager.recover_disconnected = lambda **kwargs: recovery_calls.append(kwargs) or 0
    manager.registry.subscriptions = lambda: [object()]
    manager._ensure_recovery_supervisor()
    thread = manager._recovery_thread
    assert thread is not None and thread.is_alive()
    time.sleep(0.12)
    assert recovery_calls


    manager.close()
    assert not thread.is_alive()
    calls_at_close = len(recovery_calls)
    time.sleep(0.03)
    assert len(recovery_calls) == calls_at_close



def test_recovery_supervisor_uses_bounded_backoff_after_transient_errors():
    manager = CommonWebSocketManager()
    # Test the production floor rather than an interval the loop clamps.
    manager._recovery_interval_seconds = 0.1
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
    manager.registry.subscriptions = lambda: [object()]
    manager._ensure_recovery_supervisor()
    assert recovery_succeeded.wait(timeout=1.0)
    state = manager.snapshot()
    assert state["recovery_backoff_errors"] == 2
    assert state["recovery_backoff_current_seconds"] == manager._recovery_interval_seconds
    assert state["recovery_backoff_resets"] >= 1
    assert state["recovery_backoff_max_seconds"] == 0.04
    manager.close()



def test_supervisor_preserves_pending_groups_when_recovery_fails():
    manager = CommonWebSocketManager()
    manager._recovery_interval_seconds = 0.01
    pending_group = SocketGroup(1, 3)
    manager._failure_recovery_pending_groups.add(pending_group)
    failed_pass = Event()
    manager.registry.subscriptions = lambda: [object()]

    def fail_recovery(**kwargs):
        failed_pass.set()
        raise RuntimeError("transient recovery failure")

    manager.recover_disconnected = fail_recovery
    manager._ensure_recovery_supervisor()
    assert failed_pass.wait(timeout=1.0)
    # The failed pass must not consume coalesced failures; queue remains
    # available for the next successful supervisor pass.
    assert manager.snapshot()["failure_recovery_pending_groups"] == ["1:3"]
    manager.close()


def test_late_close_completion_keeps_recovery_admission_owned():
    release_close = Event()
    close_started = Event()
    manager = CommonWebSocketManager()
    groups = [SocketGroup(1, 0), SocketGroup(1, 1)]
    sockets = [BlockingSocket(), BlockingSocket()]
    for group, socket in zip(groups, sockets):
        manager._sockets[group] = socket
        manager._socket_generation[group] = 1
        manager._socket_generation_counter[group] = 1
        manager._socket_tokens[group] = {(1, str(group.shard))}

    def delayed_close(*args, **kwargs):
        callback = kwargs["on_finished"]

        def worker_body():
            close_started.set()
            release_close.wait(timeout=2.0)
            callback()

        worker = Thread(target=worker_body, daemon=True)
        worker.start()
        return worker

    manager._close_socket_tracked = delayed_close
    manager.recover_disconnected = lambda **kwargs: 0
    manager._reconcile = lambda: None
    manager._schedule_socket_failure_recovery(groups[0], sockets[0], 1, "first failure")
    assert close_started.wait(timeout=1.0)
    assert manager.snapshot()["failure_recovery_worker_active"] is True

    manager._schedule_socket_failure_recovery(groups[1], sockets[1], 1, "second failure")
    state = manager.snapshot()
    assert state["failure_recovery_worker_active"] is True
    assert state["failure_recovery_admission_rejections"] == 1
    assert state["failure_recovery_pending_groups"] == ["1:1"]

    release_close.set()
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["failure_recovery_worker_active"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert manager.snapshot()["failure_recovery_worker_active"] is False
    manager.close()


def test_failed_late_close_recovery_releases_admission_and_keeps_coalesced_group():
    release_close = Event()
    close_started = Event()
    recovery_called = Event()
    manager = CommonWebSocketManager()
    group = SocketGroup(1, 0)
    pending = SocketGroup(1, 1)
    socket = BlockingSocket()
    manager._sockets[group] = socket
    manager._socket_generation[group] = 1
    manager._socket_generation_counter[group] = 1
    manager._socket_tokens[group] = {(1, "0")}
    # The coalesced failure must be a current socket generation; otherwise
    # admission rejects it before it can enter the pending queue.
    pending_socket = BlockingSocket()
    manager._sockets[pending] = pending_socket
    manager._socket_generation[pending] = 1
    manager._socket_generation_counter[pending] = 1
    manager._socket_tokens[pending] = {(1, "1")}

    def delayed_close(*args, **kwargs):
        callback = kwargs["on_finished"]

        def worker_body():
            close_started.set()
            release_close.wait(timeout=2.0)
            callback()

        worker = Thread(target=worker_body, daemon=True)
        worker.start()
        return worker

    def fail_recovery(**kwargs):
        recovery_called.set()
        raise RuntimeError("transient recovery error")

    manager._close_socket_tracked = delayed_close
    manager.recover_disconnected = fail_recovery
    reconciled = []
    manager._reconcile = lambda: reconciled.append(True)
    manager._schedule_socket_failure_recovery(group, socket, 1, "failure")
    assert close_started.wait(timeout=1.0)
    manager._schedule_socket_failure_recovery(pending, pending_socket, 1, "coalesced")
    assert manager.snapshot()["failure_recovery_worker_active"] is True
    release_close.set()
    assert recovery_called.wait(timeout=1.0)
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["failure_recovery_worker_active"] and time.monotonic() < deadline:
        time.sleep(0.005)
    state = manager.snapshot()
    assert state["failure_recovery_worker_active"] is False
    # The second transport completed its close and successfully reconciled the
    # full desired registry, so its pending marker must be drained.
    assert state["failure_recovery_pending_groups"] == []
    assert reconciled == [True]
    manager.close()


def test_late_failure_close_callback_after_shutdown_does_not_recover_or_reconcile():
    release_close = Event()
    close_started = Event()
    recovery_calls = []
    reconcile_calls = []
    manager = CommonWebSocketManager()
    group = SocketGroup(1, 0)
    socket = BlockingSocket()
    manager._sockets[group] = socket
    manager._socket_generation[group] = 1
    manager._socket_generation_counter[group] = 1
    manager._socket_tokens[group] = {(1, "token")}

    def deferred_close(*args, **kwargs):
        callback = kwargs.get("on_finished")

        def worker_body():
            close_started.set()
            release_close.wait(timeout=2.0)
            if callback:
                callback()

        worker = Thread(target=worker_body, daemon=True)
        worker.start()
        return worker

    manager._close_socket_tracked = deferred_close
    manager.recover_disconnected = lambda **kwargs: recovery_calls.append(kwargs) or 0
    manager._reconcile = lambda: reconcile_calls.append(True)
    manager._schedule_socket_failure_recovery(group, socket, 1, "failure")
    assert close_started.wait(timeout=1.0)
    manager.close()
    release_close.set()
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["failure_recovery_worker_active"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert recovery_calls == []
    assert reconcile_calls == []
    assert manager.snapshot()["failure_recovery_worker_active"] is False


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
    def completed_close(*args, **kwargs):
        callback = kwargs.get("on_finished")
        if callback:
            callback()
        return Thread()  # completed close; callback models the tracked watcher

    manager._close_socket_tracked = completed_close


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


def test_three_simultaneous_shard_failures_coalesce_and_drain_all_pending_groups():
    release = Event()
    started = Event()
    manager = CommonWebSocketManager()
    groups = [SocketGroup(1, shard) for shard in range(3)]
    sockets = [BlockingSocket() for _ in groups]
    for group, socket in zip(groups, sockets):
        manager._sockets[group] = socket
        manager._socket_generation[group] = 1
        manager._socket_generation_counter[group] = 1
        manager._socket_tokens[group] = {(1, str(group.shard))}

    def completed_close(*args, **kwargs):
        callback = kwargs.get("on_finished")
        if callback:
            callback()
        return Thread()

    manager._close_socket_tracked = completed_close
    recovered_groups = []

    def wait_recovery(**kwargs):
        started.set()
        release.wait(timeout=1.5)
        recovered_groups.append(kwargs.get("group"))
        return 0

    manager.recover_disconnected = wait_recovery
    manager._reconcile = lambda: None
    manager._schedule_socket_failure_recovery(groups[0], sockets[0], 1, "first failure")
    assert started.wait(timeout=1.0)
    manager._schedule_socket_failure_recovery(groups[1], sockets[1], 1, "second failure")
    manager._schedule_socket_failure_recovery(groups[2], sockets[2], 1, "third failure")
    state = manager.snapshot()
    assert state["failure_recovery_worker_active"] is True
    assert state["failure_recovery_pending_groups"] == ["1:1", "1:2"]
    release.set()
    deadline = time.monotonic() + 2.0
    while manager.snapshot()["failure_recovery_worker_active"] and time.monotonic() < deadline:
        time.sleep(0.005)
    state = manager.snapshot()
    assert state["failure_recovery_worker_active"] is False
    assert state["failure_recovery_pending_groups"] == []
    assert len(recovered_groups) == 3
    manager.close()


def test_shutdown_clears_coalesced_failure_recovery_queue():
    manager = CommonWebSocketManager()
    manager._failure_recovery_pending_groups.add(SocketGroup(1, 2))
    manager._failure_recovery_groups.add(SocketGroup(1, 1))
    manager.close()
    state = manager.snapshot()
    assert state["failure_recovery_pending_groups"] == []
    assert state["failure_recovery_groups"] == []


def test_snapshot_reports_registry_socket_token_mismatch_and_clears_when_aligned():
    manager = CommonWebSocketManager()
    key = InstrumentKey("NSE", "NSE", "IDX-101")
    manager.registry.register(InstrumentDescriptor(
        key=key, symbol="NIFTY", instrument_type="index", exchange="NSE",
        segment="NSE", underlying="NIFTY",
    ))
    manager.registry.subscribe("audit-consumer", key, mode=1)
    group = SocketGroup(1, 0)

    state = manager.snapshot()
    mismatch = state["socket_token_set_mismatches"]
    assert len(mismatch) == 1
    assert mismatch[0]["group"] == "1:0"
    assert mismatch[0]["missing_from_socket"]
    assert mismatch[0]["unexpected_on_socket"] == []

    expected = (manager._resolve_exchange_type(key), key.token)
    manager._socket_tokens[group] = {expected, (expected[0], "OLD-999")}
    mismatch = manager.snapshot()["socket_token_set_mismatches"]
    assert len(mismatch) == 1
    assert mismatch[0]["missing_from_socket"] == []
    assert mismatch[0]["unexpected_on_socket"] == [(expected[0], "OLD-999")]

    manager._socket_tokens[group] = {expected}
    assert manager.snapshot()["socket_token_set_mismatches"] == []
    manager.close()


def test_late_socket_operation_failure_is_reported_separately_after_timeout():
    release = Event()
    manager = CommonWebSocketManager()
    group = SocketGroup(1, 0)
    socket = type("Socket", (), {"connected": True})()
    manager._socket_call_wait_seconds = 0.01
    manager._sockets[group] = socket
    manager._socket_tokens[group] = {(1, "123")}
    manager._close_socket_tracked = lambda *args, **kwargs: Thread()

    def delayed_failure():
        release.wait(timeout=1.0)
        raise RuntimeError("late unsubscribe failure")

    try:
        manager._run_socket_operation_bounded(
            socket=socket, group=group, operation=delayed_failure, operation_name="unsubscribe"
        )
    except TimeoutError:
        pass
    else:
        raise AssertionError("operation should timeout before its late failure")

    assert group not in manager._sockets
    assert group not in manager._socket_tokens
    release.set()
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["socket_operation_worker_count"] and time.monotonic() < deadline:
        time.sleep(0.005)
    state = manager.snapshot()
    assert state["socket_operation_worker_late_completions"] == 1
    assert state["socket_operation_worker_late_failures"] == 1
    manager.close()


def test_manager_shutdown_waits_for_active_socket_operation_before_close():
    release_operation = Event()
    close_started = Event()
    manager = CommonWebSocketManager()
    group = SocketGroup(1, 0)
    sock = type("Socket", (), {"connected": True, "close": lambda self: close_started.set()})()
    manager._sockets[group] = sock
    manager._socket_operation_workers["manual-op"] = {"group": "1:0", "worker_alive": True}
    manager._socket_operation_events["manual-op"] = release_operation
    shutdown_thread = Thread(target=manager.close, daemon=True)
    shutdown_thread.start()
    time.sleep(0.05)
    assert not close_started.is_set(), "close must not race a tracked SDK mutation"
    release_operation.set()
    shutdown_thread.join(timeout=2.0)
    assert not shutdown_thread.is_alive()
    assert close_started.is_set()


def test_socket_operation_is_rejected_after_manager_shutdown():
    manager = CommonWebSocketManager()
    manager.close()
    called = []
    try:
        manager._run_socket_operation_bounded(
            socket=BlockingSocket(), group=SocketGroup(1, 0),
            operation=lambda: called.append(True), operation_name="subscribe"
        )
    except RuntimeError as exc:
        assert "manager is closed" in str(exc)
    else:
        raise AssertionError("closed manager must reject broker mutations")
    assert called == []
    assert manager.snapshot()["socket_operation_worker_count"] == 0


def test_coalesced_failure_close_callback_after_shutdown_does_not_reconcile():
    callbacks = []
    manager = CommonWebSocketManager()
    groups = [SocketGroup(1, 0), SocketGroup(1, 1)]
    sockets = [BlockingSocket(), BlockingSocket()]
    for group, socket in zip(groups, sockets):
        manager._sockets[group] = socket
        manager._socket_generation[group] = 1
        manager._socket_generation_counter[group] = 1
        manager._socket_tokens[group] = {(1, str(group.shard))}

    manager._close_socket_tracked = lambda *args, **kwargs: (callbacks.append(kwargs.get("on_finished")) or Thread())
    reconciles = []
    manager.recover_disconnected = lambda **kwargs: 0
    manager._reconcile = lambda: reconciles.append(True)
    manager._schedule_socket_failure_recovery(groups[0], sockets[0], 1, "primary")
    deadline = time.monotonic() + 1.0
    while len(callbacks) < 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert len(callbacks) == 1
    manager._schedule_socket_failure_recovery(groups[1], sockets[1], 1, "coalesced")
    deadline = time.monotonic() + 1.0
    while len(callbacks) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert len(callbacks) == 2

    manager.close()
    callbacks[0]()
    callbacks[1]()
    assert reconciles == []
    assert manager.snapshot()["failure_recovery_worker_active"] is False
    assert manager.snapshot()["failure_recovery_pending_groups"] == []



def test_late_socket_operation_completion_after_shutdown_cannot_reopen_transport():
    release_operation = Event()
    manager = CommonWebSocketManager()
    group = SocketGroup(1, 0)
    socket = BlockingSocket()
    manager._socket_call_wait_seconds = 0.01
    reconcile_calls = []
    manager._reconcile = lambda: reconcile_calls.append(True)
    manager._sockets[group] = socket
    manager._socket_tokens[group] = {(1, "late-token")}



    try:
        manager._run_socket_operation_bounded(
            socket=socket, group=group,
            operation=lambda: release_operation.wait(timeout=2.0),
            operation_name="subscribe",
        )
    except TimeoutError:
        pass
    else:
        raise AssertionError("broker operation should time out before shutdown")



    assert manager.snapshot()["socket_operation_worker_count"] == 1
    assert group not in manager._sockets
    manager.close()
    release_operation.set()
    deadline = time.monotonic() + 1.0
    while manager.snapshot()["socket_operation_worker_count"] and time.monotonic() < deadline:
        time.sleep(0.005)
    state = manager.snapshot()
    assert state["socket_operation_worker_count"] == 0
    assert state["socket_operation_worker_late_completions"] == 1
    assert manager._sockets == {}
    assert manager._socket_tokens == {}
    assert reconcile_calls == []
    assert manager._closed is True
