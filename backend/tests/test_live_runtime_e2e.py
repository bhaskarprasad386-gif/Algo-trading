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
    assert "1:3" in snapshot["connected_groups"]
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
    assert elapsed < 1.0
