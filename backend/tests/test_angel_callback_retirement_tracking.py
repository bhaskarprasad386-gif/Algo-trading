from threading import Event, Thread
import time

from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup


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
