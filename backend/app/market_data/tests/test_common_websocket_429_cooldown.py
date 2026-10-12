"""Focused regression tests for Angel One 429 connect admission cooldown."""
from threading import Event, Thread
import time

from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry


class CooldownSocket:
    instances = []

    def __init__(self):
        self.connect_calls = []
        self.connected = False
        self.connecting = False
        self.closed = False
        type(self).instances.append(self)

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        self.connected = True

    def subscribe_groups(self, groups, mode=None):
        pass

    def unsubscribe_groups(self, groups):
        pass

    def close(self):
        self.closed = True
        self.connected = False


def test_async_429_callback_publishes_cooldown_without_admission_lock():
    CooldownSocket.instances.clear()
    registry = InstrumentRegistry()
    key = InstrumentKey("NSE", "EQ", "101")
    registry.register(InstrumentDescriptor(
        key=key,
        symbol="S101",
        instrument_type="equity",
        exchange="NSE",
        segment="EQ",
    ))
    manager = CommonWebSocketManager(registry, socket_factory=CooldownSocket)
    manager._recovery_interval_seconds = 60.0
    callback_returned = Event()
    try:
        manager.subscribe("cash", [key])
        socket = CooldownSocket.instances[0]
        failure = socket.connect_calls[0]["on_failure"]

        worker = Thread(
            target=lambda: (failure("429 Connection Limit Exceeded"), callback_returned.set()),
            daemon=True,
        )
        worker.start()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if manager._broker_cooldown_remaining() > 0:
                break
            time.sleep(0.005)
        assert manager._broker_cooldown_remaining() > 0, (
            "429 callback did not publish cooldown promptly"
        )

        assert callback_returned.wait(timeout=1.0)
        assert manager.snapshot()["broker_cooldown_remaining_seconds"] > 0
    finally:
        manager.close()
