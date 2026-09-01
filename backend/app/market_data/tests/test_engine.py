from app.market_data.engine import MarketDataEngine


class FakeWebSocket:
    def __init__(self):
        self.started = None
        self.subscribed = []
        self.unsubscribed = []
        self.closed = False

    def connect(self, **kwargs):
        self.started = kwargs
        kwargs["on_data"]({"symbol": "NIFTY", "ltp": 25000})

    def subscribe(self, tokens, mode=None):
        self.subscribed.append((tokens, mode))

    def unsubscribe(self, tokens):
        self.unsubscribed.append(tokens)

    def close(self):
        self.closed = True


def test_engine_routes_tick_and_callback():
    ws = FakeWebSocket()
    received = []
    engine = MarketDataEngine(websocket=ws)

    engine.start(1, ["99926000"], on_tick=received.append)

    assert engine.latest("NIFTY")["ltp"] == 25000
    assert received[0]["symbol"] == "NIFTY"
    assert ws.started["tokens"] == ["99926000"]


def test_engine_subscription_and_close():
    ws = FakeWebSocket()
    engine = MarketDataEngine(websocket=ws)
    engine.start(1, [])
    engine.subscribe(["123"], mode=2)
    engine.unsubscribe(["123"])
    engine.close()

    assert ws.subscribed == [(["123"], 2)]
    assert ws.unsubscribed == [["123"]]
    assert ws.closed is True
