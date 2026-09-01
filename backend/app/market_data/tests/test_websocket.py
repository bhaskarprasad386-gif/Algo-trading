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
    )


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
