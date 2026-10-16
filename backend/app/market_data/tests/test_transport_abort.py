from threading import Event, Thread
from types import SimpleNamespace

from app.market_data.websocket import MarketDataWebSocket


class AbortableLowLevelSocket:
    def __init__(self, release: Event):
        self.release = release
        self.abort_called = False

    def abort(self):
        self.abort_called = True
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
