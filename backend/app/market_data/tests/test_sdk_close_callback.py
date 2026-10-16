from threading import Event, Thread
from types import SimpleNamespace

import app.market_data.websocket as websocket_module


class FakeWebSocketApp:
    def __init__(self, url, **callbacks):
        self.callbacks = callbacks
        self.sock = SimpleNamespace()
        self.closed = Event()

    def run_forever(self, **kwargs):
        self.callbacks["on_open"](self)
        # websocket-client's real callback shape includes status and reason.
        self.callbacks["on_close"](self, 1000, "normal closure")
        self.closed.set()


def test_sdk_close_callback_adapts_websocket_client_signature(monkeypatch):
    calls = []
    socket = SimpleNamespace(
        auth_token="jwt",
        api_key="key",
        client_code="client",
        feed_token="feed",
        ROOT_URI="wss://example.invalid",
        _on_open=lambda wsapp: None,
        _on_error=lambda wsapp, error: None,
        _on_close=lambda wsapp: calls.append(wsapp),
        _on_data=lambda *args: None,
        _on_ping=lambda *args: None,
        _on_pong=lambda *args: None,
        _connect_handshake_timeout_seconds=1.0,
    )
    monkeypatch.setattr(websocket_module.websocket_client, "WebSocketApp", FakeWebSocketApp)

    websocket_module._connect_sdk_socket_with_verified_tls(socket)

    transport = socket._transport_thread
    assert transport is not None
    transport.join(timeout=1.0)
    assert not transport.is_alive()
    assert len(calls) == 1
