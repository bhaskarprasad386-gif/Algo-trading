from types import SimpleNamespace

from app.market_data import websocket as websocket_module
from app.market_data.websocket import MarketDataWebSocket


class FakeSocket:
    instances = []

    def __init__(self, *args):
        self.args = args
        self.on_open = None
        self.on_data = None
        self.on_error = None
        self.on_close = None
        self.subscriptions = []
        self.unsubscriptions = []
        self.closed = False
        self.__class__.instances.append(self)

    def connect(self):
        self.on_open(self)

    def subscribe(self, correlation_id, mode, payload):
        self.subscriptions.append((correlation_id, mode, payload))

    def unsubscribe(self, correlation_id, mode, payload):
        self.unsubscriptions.append((correlation_id, mode, payload))

    def close_connection(self):
        self.closed = True


def make_auth():
    return SimpleNamespace(
        smart_api=object(),
        session_data={"jwtToken": "jwt", "feedToken": "feed"},
        api_key="api-key",
        client_id="client",
        ensure_session_fresh=lambda max_age: None,
    )


def test_websocket_refreshes_auth_before_building_socket(monkeypatch):
    calls = []
    auth = make_auth()
    auth.ensure_session_fresh = lambda max_age: calls.append(max_age)
    client = MarketDataWebSocket(auth=auth)
    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", FakeSocket)
    client._build_socket()
    assert calls == [websocket_module.settings.ANGEL_SESSION_REFRESH_INTERVAL_SECONDS]


def test_websocket_connect_subscribe_and_unsubscribe(monkeypatch):
    FakeSocket.instances.clear()
    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", FakeSocket)

    received = []
    client = MarketDataWebSocket(auth=make_auth())
    client.connect(1, ["101", "101", "202"], on_data=received.append)

    socket = FakeSocket.instances[-1]
    assert client.connected is True
    assert client.tokens == ["101", "202"]
    assert socket.subscriptions

    socket.on_data(socket, {"token": "101", "last_traded_price": 12345})
    assert received == [{"token": "101", "last_traded_price": 12345}]

    client.subscribe(["303"])
    assert client.tokens == ["303"]
    assert socket.subscriptions[-1][2][0]["tokens"] == ["303"]

    client.unsubscribe(["303"])
    assert client.tokens == []
    assert socket.unsubscriptions[-1][2][0]["tokens"] == ["303"]

    client.close()
    assert socket.closed is True
    assert client.connected is False


def test_websocket_connect_retries_after_start_failure(monkeypatch):
    calls = {"count": 0}

    class RetrySocket(FakeSocket):
        def connect(self):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("temporary failure")
            self.on_open(self)

    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", RetrySocket)

    client = MarketDataWebSocket(auth=make_auth())
    client.connect(
        1,
        ["101"],
        reconnect_attempts=1,
        reconnect_delay_seconds=0,
    )

    assert calls["count"] == 2
    assert client.connected is True


def test_websocket_unexpected_close_schedules_reconnect(monkeypatch):
    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", FakeSocket)
    client = MarketDataWebSocket(auth=make_auth())
    scheduled = []
    monkeypatch.setattr(client, "_schedule_reconnect", lambda: scheduled.append(True))
    socket = client._build_socket()
    socket.on_close(socket)
    assert scheduled == [True]


def test_websocket_health_tracks_errors_and_resets_on_connect(monkeypatch):
    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", FakeSocket)
    client = MarketDataWebSocket(auth=make_auth())
    socket = client._build_socket()
    socket.on_error(socket, "temporary")
    health = client.health
    assert health["error_count"] == 1
    assert health["consecutive_failures"] == 1
    assert health["last_error"] == "temporary"
    socket.on_open(socket)
    health = client.health
    assert health["consecutive_failures"] == 0
    assert health["last_error"] is None


def test_websocket_reconnect_backoff_is_bounded(monkeypatch):
    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", FakeSocket)
    client = MarketDataWebSocket(auth=make_auth())
    client.exchange_type = 1
    client.tokens = ["101"]
    client._reconnect_delay_seconds = 10.0
    client._consecutive_failures = 6
    sleeps = []
    monkeypatch.setattr(websocket_module.time, "sleep", lambda value: sleeps.append(value))
    monkeypatch.setattr(client, "connect", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(client, "_schedule_reconnect", lambda: None)
    client._reconnect_after_disconnect()
    assert sleeps == [60.0]
    assert client.health["error_count"] == 1
    assert client.health["consecutive_failures"] == 7


def test_websocket_supports_multiple_exchange_groups(monkeypatch):
    FakeSocket.instances.clear()
    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", FakeSocket)
    client = MarketDataWebSocket(auth=make_auth())
    client.connect(subscriptions={1: ["101"], 2: ["202"], 5: ["303"]})
    socket = FakeSocket.instances[-1]
    assert socket.subscriptions[-1][2] == [
        {"exchangeType": 1, "tokens": ["101"]},
        {"exchangeType": 2, "tokens": ["202"]},
        {"exchangeType": 5, "tokens": ["303"]},
    ]
    client.subscribe_groups({4: ["404"]})
    assert client.subscriptions[4] == ["404"]
    assert {"exchangeType": 4, "tokens": ["404"]} in socket.subscriptions[-1][2]
    client.unsubscribe_groups({4: ["404"]})
    assert 4 not in client.subscriptions



def test_websocket_can_disable_background_reconnect_for_shared_manager(monkeypatch):
    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", FakeSocket)
    client = MarketDataWebSocket(auth=make_auth(), auto_reconnect=False)
    scheduled = []
    monkeypatch.setattr(client, "_schedule_reconnect", lambda: scheduled.append(True))
    socket = client._build_socket()
    socket.on_close(socket)
    assert scheduled == []


def test_websocket_marks_async_handshake_as_connecting(monkeypatch):
    class AsyncSocket(FakeSocket):
        def connect(self):
            return None

    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", AsyncSocket)
    client = MarketDataWebSocket(auth=make_auth(), auto_reconnect=False)
    client.connect(1, ["101"], reconnect_attempts=0)
    assert client.connected is False
    assert client.connecting is True


def test_websocket_failure_callbacks_notify_manager_on_error_and_close(monkeypatch):
    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", FakeSocket)
    client = MarketDataWebSocket(auth=make_auth(), auto_reconnect=False)
    failures = []
    client.connect(1, ["101"], on_failure=failures.append)
    socket = client.websocket
    socket.on_error(socket, "temporary DNS failure")
    assert failures == ["temporary DNS failure"]
    socket.on_close(socket)
    assert failures == ["temporary DNS failure", "socket closed"]


def test_blocking_initial_subscribe_notifies_failure_and_does_not_stall_on_open(monkeypatch):
    from threading import Event

    release = Event()
    class BlockingSubscribeSocket(FakeSocket):
        def subscribe(self, correlation_id, mode, payload):
            release.wait(timeout=1.0)

    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", BlockingSubscribeSocket)
    client = MarketDataWebSocket(auth=make_auth(), auto_reconnect=False)
    client._socket_operation_timeout_seconds = 0.02
    failures = []
    client.connect(1, ["101"], on_failure=failures.append)
    assert failures == ["initial subscribe exceeded 0.02s"]
    assert client.connected is False
    release.set()
    client.close()

def test_websocket_rejects_missing_auth_tokens_before_opening_socket(monkeypatch):
    FakeSocket.instances.clear()
    auth = make_auth()
    auth.session_data = {"jwtToken": "jwt", "feedToken": ""}
    monkeypatch.setattr(websocket_module, "SmartWebSocketV2", FakeSocket)
    client = MarketDataWebSocket(auth=auth, auto_reconnect=False)

    try:
        client._build_socket()
    except Exception as exc:
        assert getattr(exc, "name", None) == "WebSocketAuthError"
        assert getattr(exc, "message", "") == "JWT token or feed token is missing."
    else:
        raise AssertionError("missing feed token must prevent socket creation")

    assert FakeSocket.instances == []

