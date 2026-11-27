from threading import Event, Thread
import time

from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry


class BlockingSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_shutdown_fence_prevents_socket_created_during_reconcile_from_being_admitted():
    registry = InstrumentRegistry()
    key = InstrumentKey("NSE", "EQ", "shutdown-race-991")
    registry.register(InstrumentDescriptor(
        key=key, symbol="SHUTDOWN-RACE", instrument_type="equity",
        exchange="NSE", segment="EQ",
    ))
    entered_factory = Event()
    release_factory = Event()
    created = []

    def socket_factory():
        entered_factory.set()
        assert release_factory.wait(timeout=2.0)
        socket = BlockingSocket()
        created.append(socket)
        return socket

    manager = CommonWebSocketManager(registry, socket_factory=socket_factory)
    errors = []

    def subscribe():
        try:
            manager.subscribe("shutdown-race", [key])
        except Exception as exc:
            errors.append(exc)

    subscribe_thread = Thread(target=subscribe)
    subscribe_thread.start()
    assert entered_factory.wait(timeout=1.0)

    close_thread = Thread(target=manager.close)
    close_thread.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        with manager._lock:
            if manager._closed:
                break
        time.sleep(0.005)
    with manager._lock:
        assert manager._closed

    release_factory.set()
    subscribe_thread.join(timeout=2.0)
    close_thread.join(timeout=3.0)
    assert not subscribe_thread.is_alive()
    assert not close_thread.is_alive()
    assert created
    deadline = time.monotonic() + 1.0
    while not created[0].closed and time.monotonic() < deadline:
        time.sleep(0.005)
    assert created[0].closed
    assert manager.snapshot()["socket_groups"] == 0
