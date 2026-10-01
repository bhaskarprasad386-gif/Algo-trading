from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor


class FakeSocket:
    def __init__(self):
        self.connected = True
        self.closed = False
    def connect(self, **kwargs):
        self.connected = True
    def subscribe(self, tokens, mode=None):
        return None
    def subscribe_groups(self, groups, mode=None):
        return None
    def unsubscribe_groups(self, groups):
        return None
    def close(self):
        self.closed = True
        self.connected = False


def test_common_websocket_snapshot_reports_connected_group():
    socket = FakeSocket()
    manager = CommonWebSocketManager(socket_factory=lambda: socket)
    key = InstrumentKey("NSE", "NSE", "101")
    manager.registry.register(
        InstrumentDescriptor(
            key=key, symbol="TEST-EQ", instrument_type="equity",
            exchange="NSE", segment="NSE",
        )
    )
    manager.subscribe("cash-future", [key], mode=3)
    snapshot = manager.snapshot()
    assert snapshot["subscriptions"] == 1
    assert snapshot["active_instruments"] == 1
    assert "3:0" in snapshot["connected_groups"]
    manager.close()
    assert socket.closed


def test_common_websocket_close_does_not_block_on_broker_close():
    import time

    class BlockingSocket(FakeSocket):
        def close(self):
            time.sleep(10)

    socket = BlockingSocket()
    manager = CommonWebSocketManager(socket_factory=lambda: socket)
    started = time.monotonic()
    manager._close_socket_bounded(socket, timeout=0.05)
    elapsed = time.monotonic() - started
    assert elapsed < 3.0


def test_common_websocket_clear_consumer_does_not_block_on_broker_close():
    import time

    class BlockingSocket(FakeSocket):
        def close(self):
            time.sleep(10)

    socket = BlockingSocket()
    manager = CommonWebSocketManager(socket_factory=lambda: socket)
    key = InstrumentKey("NSE", "NSE", "202")
    manager.registry.register(
        InstrumentDescriptor(
            key=key, symbol="TEST-EQ-2", instrument_type="equity",
            exchange="NSE", segment="NSE",
        )
    )
    manager.subscribe("cash-future", [key], mode=3)
    started = time.monotonic()
    manager.clear_consumer("cash-future")
    elapsed = time.monotonic() - started
    assert elapsed < 3.0

def test_common_websocket_snapshot_does_not_wait_for_broker_connect():
    import threading
    import time

    connect_started = threading.Event()
    release_connect = threading.Event()

    class SlowConnectSocket(FakeSocket):
        def connect(self, **kwargs):
            connect_started.set()
            release_connect.wait(timeout=2.0)
            self.connected = True

    socket = SlowConnectSocket()
    manager = CommonWebSocketManager(socket_factory=lambda: socket)
    key = InstrumentKey("NSE", "NSE", "303")
    manager.registry.register(
        InstrumentDescriptor(
            key=key, symbol="TEST-EQ-3", instrument_type="equity",
            exchange="NSE", segment="NSE",
        )
    )

    worker = threading.Thread(
        target=lambda: manager.subscribe("cash-future", [key], mode=3),
        daemon=True,
    )
    worker.start()
    assert connect_started.wait(timeout=1.0)

    started = time.monotonic()
    snapshot = manager.snapshot()
    elapsed = time.monotonic() - started

    release_connect.set()
    worker.join(timeout=2.0)

    assert elapsed < 0.5
    assert snapshot["subscriptions"] == 1
    assert snapshot["active_instruments"] == 1
