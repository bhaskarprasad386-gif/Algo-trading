from unittest.mock import Mock

from app.market_data.websocket import MarketDataWebSocket


class FakeAuth:
    smart_api = object()
    session_data = {"jwtToken": "jwt", "feedToken": "feed"}
    api_key = "key"
    client_id = "client"

    def login(self):
        raise AssertionError("login should not be needed")


def test_subscribe_deduplicates_tokens_and_unsubscribe_updates_state():
    ws = MarketDataWebSocket(auth=FakeAuth())
    ws.exchange_type = 1
    ws.correlation_id = "test"
    ws.subscribe(["1", "1", "2"])
    assert ws.tokens == ["1", "2"]
    ws.unsubscribe(["1"])
    assert ws.tokens == ["2"]


def test_close_without_socket_is_safe():
    ws = MarketDataWebSocket(auth=FakeAuth())
    ws.close()
    assert ws.connected is False
    assert ws.websocket is None


def test_connected_property_defaults_false():
    ws = MarketDataWebSocket(auth=FakeAuth())
    assert ws.connected is False
