from threading import Event, Thread
from time import sleep
from types import SimpleNamespace

from app.market_data.websocket import MarketDataWebSocket, _close_sdk_connection_bounded


class AbortableLowLevelSocket:
    def __init__(self, release: Event):
        self.release = release
        self.abort_called = False

    def abort(self):
        self.abort_called = True
        # Simulate websocket-client still unwinding its bounded select/poll wait
        # after abort() has closed the underlying descriptor.
        Thread(target=self._release_after_poll, daemon=True).start()

    def _release_after_poll(self):
        sleep(1.2)
        self.release.set()


def make_auth():
    return SimpleNamespace(
        smart_api=object(),
        session_data={"jwtToken": "jwt", "feedToken": "feed"},
        api_key="api-key",
        client_id="client",
        ensure_session_fresh=lambda max_age: None,
    )


def test_close_aborts_blocked_transport_after_graceful_timeout():
    release = Event()
    low_level = AbortableLowLevelSocket(release)
    transport = Thread(target=lambda: release.wait(timeout=5.0), daemon=True)
    transport.start()

    sdk_socket = SimpleNamespace(
        wsapp=SimpleNamespace(sock=low_level),
        close_connection=lambda: None,
    )
    client = MarketDataWebSocket(auth=make_auth(), auto_reconnect=False)
    client.websocket = sdk_socket
    client._transport_thread = transport

    client.close()

    assert low_level.abort_called is True
    assert not transport.is_alive()
    assert client.health["transport_close_timed_out"] is False



def test_close_bounds_a_hung_sdk_close_connection_call():
    release = Event()
    low_level = AbortableLowLevelSocket(release)
    transport = Thread(target=lambda: release.wait(timeout=5.0), daemon=True)
    transport.start()

    def hung_close_connection():
        Event().wait(timeout=30.0)

    sdk_socket = SimpleNamespace(
        wsapp=SimpleNamespace(sock=low_level),
        close_connection=hung_close_connection,
    )
    client = MarketDataWebSocket(auth=make_auth(), auto_reconnect=False)
    client.websocket = sdk_socket
    client._transport_thread = transport

    started = __import__("time").monotonic()
    client.close()
    elapsed = __import__("time").monotonic() - started

    assert elapsed < 10.0
    assert low_level.abort_called is True
    assert not transport.is_alive()
    assert client.health["transport_close_timed_out"] is False

def test_sdk_close_callback_path_is_bounded_and_aborts_low_level_socket():
    release_transport = Event()
    low_level = AbortableLowLevelSocket(release_transport)
    transport = Thread(target=lambda: release_transport.wait(timeout=5.0), daemon=True)
    transport.start()
    release_close_worker = Event()

    def hung_close_connection():
        release_close_worker.wait(timeout=30.0)

    sdk_socket = SimpleNamespace(
        wsapp=SimpleNamespace(sock=low_level),
        close_connection=hung_close_connection,
    )

    from time import monotonic
    started = monotonic()
    closed = _close_sdk_connection_bounded(
        sdk_socket, phase="test-callback-close", timeout_seconds=0.05
    )
    elapsed = monotonic() - started
    transport.join(timeout=2.0)
    release_close_worker.set()

    assert elapsed < 1.0
    assert closed is False
    assert low_level.abort_called is True
    assert not transport.is_alive()




def test_late_connect_completion_cannot_resurrect_socket_after_close():
    entered = Event()
    release = Event()

    class BlockingConnectSocket:
        _transport_thread = None

        def connect(self):
            entered.set()
            release.wait(timeout=5.0)

        def close_connection(self):
            release.set()

    sdk_socket = BlockingConnectSocket()
    client = MarketDataWebSocket(auth=make_auth(), auto_reconnect=False)
    client._build_socket = lambda: sdk_socket
    errors = []

    def run_connect():
        try:
            client.connect(
                subscriptions={1: ["123"]},
                reconnect_attempts=0,
                reconnect_delay_seconds=0,
            )
        except Exception as exc:
            errors.append(exc)

    worker = Thread(target=run_connect, daemon=True)
    worker.start()
    assert entered.wait(timeout=2.0)

    client.close()
    release.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert errors == []
    assert client.websocket is None
    assert client.health["stopping"] is True
    assert client.health["connected"] is False



def test_retired_socket_callbacks_cannot_mutate_or_deliver_to_current_wrapper(monkeypatch):
    from app.market_data import websocket as websocket_module

    class FakeSdkSocket:
        def __init__(self, *args):
            self.on_open = None
            self.on_data = None
            self.on_error = None
            self.on_close = None

        def close_connection(self):
            return None

    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", FakeSdkSocket)
    client = MarketDataWebSocket(auth=make_auth(), auto_reconnect=False)
    delivered = []
    failures = []
    client.on_data = delivered.append
    client.on_failure = failures.append
    socket = client._build_socket()
    client.websocket = socket
    client.close()
    before = client.health

    socket.on_open(None)
    socket.on_data(None, {"token": "123"})
    socket.on_error(None, RuntimeError("late retired error"))
    socket.on_close(None)

    after = client.health
    assert delivered == []
    assert failures == []
    assert after["connected"] is False
    assert after["connecting"] is False
    assert after["error_count"] == before["error_count"]
    assert client.websocket is None
