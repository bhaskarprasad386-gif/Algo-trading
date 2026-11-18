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
