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

class CooldownOnFirstConnectSocket(CooldownSocket):
    manager = None
    first_connect_seen = Event()

    def connect(self, **kwargs):
        if not type(self).first_connect_seen.is_set():
            type(self).first_connect_seen.set()
            type(self).manager._apply_broker_cooldown("429 Connection Limit Exceeded")
        super().connect(**kwargs)


def test_queued_shard_rechecks_429_cooldown_after_connect_admission():
    CooldownOnFirstConnectSocket.instances.clear()
    CooldownOnFirstConnectSocket.first_connect_seen.clear()
    registry = InstrumentRegistry()
    descriptors = []
    for token in ("101", "102"):
        key = InstrumentKey("NSE", "EQ", token)
        descriptors.append(InstrumentDescriptor(
            key=key,
            symbol=f"S{token}",
            instrument_type="equity",
            exchange="NSE",
            segment="EQ",
        ))
    registry.register_many(descriptors)
    manager = CommonWebSocketManager(
        registry, socket_factory=CooldownOnFirstConnectSocket
    )
    CooldownOnFirstConnectSocket.manager = manager
    manager._max_tokens_per_socket = 1
    manager._broker_cooldown_seconds = 0.3
    manager._recovery_interval_seconds = 60.0
    try:
        manager.subscribe("cash", [item.key for item in descriptors])
        assert CooldownOnFirstConnectSocket.first_connect_seen.wait(timeout=1.0)
        assert len(CooldownOnFirstConnectSocket.instances) == 2

        # The second shard was queued behind the first handshake. It must
        # re-check cooldown after admission rather than connect immediately.
        second = CooldownOnFirstConnectSocket.instances[1]
        assert second.connect_calls == []

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and not second.connect_calls:
            time.sleep(0.01)
        assert len(second.connect_calls) == 1
        assert manager.snapshot()["connected_groups"] == ["1:0", "1:1"]
    finally:
        manager.close()
        CooldownOnFirstConnectSocket.manager = None

