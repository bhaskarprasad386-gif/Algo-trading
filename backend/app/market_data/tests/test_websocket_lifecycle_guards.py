from __future__ import annotations

from pathlib import Path

from app.market_data import common_strategy_feed
from app.market_data import websocket as market_websocket


class FakeWebSocketApp:
    last_instance = None

    def __init__(self, url, **kwargs):
        self.url = url
        self.callbacks = kwargs
        type(self).last_instance = self

    def run_forever(self, **kwargs):
        self.run_kwargs = kwargs


class FakeSdkSocket:
    ROOT_URI = "wss://example.invalid"
    auth_token = "jwt"
    api_key = "key"
    client_code = "client"
    feed_token = "feed"

    def __init__(self):
        self.close_calls = []
        self.on_close = lambda wsapp: self.close_calls.append(wsapp)

    def _on_open(self, wsapp):
        pass

    def _on_error(self, wsapp, error):
        pass

    def _on_close(self, wsapp):
        self.on_close(wsapp)

    def _on_data(self, *args):
        pass

    def _on_ping(self, *args):
        pass

    def _on_pong(self, *args):
        pass


def test_sdk_close_callback_accepts_websocket_client_three_argument_signature(monkeypatch):
    monkeypatch.setattr(market_websocket.websocket_client, "WebSocketApp", FakeWebSocketApp)
    socket = FakeSdkSocket()

    market_websocket._connect_sdk_socket_with_verified_tls(socket)

    app = FakeWebSocketApp.last_instance
    assert app is not None
    app.callbacks["on_close"](app, 1006, "connection lost")
    assert socket.close_calls == [app]


def test_process_admission_lock_rejects_second_local_app_instance(monkeypatch, tmp_path: Path):
    first_handle = common_strategy_feed._PROCESS_ADMISSION_HANDLE
    monkeypatch.setattr(common_strategy_feed, "_PROCESS_ADMISSION_HANDLE", None)
    monkeypatch.setattr(
        common_strategy_feed,
        "_PROCESS_ADMISSION_PATH",
        str(tmp_path / "angel-ws.lock"),
    )
    try:
        common_strategy_feed._acquire_process_admission()
        first = common_strategy_feed._PROCESS_ADMISSION_HANDLE
        assert first is not None
        # A separate file descriptor cannot take the same exclusive flock.
        import fcntl

        second = open(common_strategy_feed._PROCESS_ADMISSION_PATH, "a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(second.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise AssertionError("duplicate local process should not acquire Angel admission lock")
        finally:
            second.close()
    finally:
        handle = common_strategy_feed._PROCESS_ADMISSION_HANDLE
        if handle is not None:
            handle.close()
        monkeypatch.setattr(common_strategy_feed, "_PROCESS_ADMISSION_HANDLE", first_handle)
