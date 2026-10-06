from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor


class FakeSocket:
    def __init__(self):
        self.connected = []
        self.subscribed = []
        self.group_subscriptions = []
        self.closed = False

    def connect(self, **kwargs):
        self.connected.append(kwargs)

    def subscribe(self, tokens, mode=1):
        self.subscribed.append((tuple(tokens), mode))

    def subscribe_groups(self, groups, mode=1):
        self.group_subscriptions.append((groups, mode))

    def close(self):
        self.closed = True


def test_strategy_feed_registers_canonical_descriptors_on_common_manager():
    sockets = []

    def factory():
        socket = FakeSocket()
        sockets.append(socket)
        return socket

    manager = CommonWebSocketManager(socket_factory=factory)
    feed = CommonStrategyMarketFeed("calendar-test", manager=manager)
    descriptor = feed.descriptor(
        exchange="NFO",
        token="123",
        symbol="NIFTY30SEP26FUT",
        instrument_type="future",
        expiry="2026-09-30",
        lot_size=75,
    )
    received = []
    feed.start([descriptor], received.append)

    assert manager.snapshot()["subscriptions"] == 1
    assert manager.snapshot()["consumers"] == ["calendar-test"]
    assert sockets[0].connected[0]["mode"] == 3
    assert sockets[0].connected[0]["subscriptions"] == {2: ["123"]}

    sockets[0].connected[0]["on_data"]({
        "token": "123",
        "exchange_timestamp": 1727000000000,
        "last_traded_price": 2500000,
        "best_5_buy_data": [{"price": 2499900, "quantity": 10}],
        "best_5_sell_data": [{"price": 2500100, "quantity": 12}],
    })
    assert len(received) == 1
    assert received[0].instrument == InstrumentKey("NFO", "NFO", "123")
    assert received[0].ltp == 25000.0
    assert received[0].bid == 24999.0
    assert received[0].ask == 25001.0

    feed.stop()
    assert manager.snapshot()["subscriptions"] == 0
    assert sockets[0].closed


def test_strategy_feed_reuses_same_manager_for_multiple_consumers():
    manager = CommonWebSocketManager(socket_factory=FakeSocket)
    first = CommonStrategyMarketFeed("calendar", manager=manager)
    second = CommonStrategyMarketFeed("box", manager=manager)
    d1 = first.descriptor(exchange="NFO", token="101", symbol="A", instrument_type="future")
    d2 = second.descriptor(exchange="NFO", token="102", symbol="B", instrument_type="option",
                            expiry="2026-10-29", strike=100.0, option_type="CE")
    first.start([d1], lambda _: None)
    second.start([d2], lambda _: None)

    snapshot = manager.snapshot()
    assert snapshot["subscriptions"] == 2
    assert snapshot["socket_groups"] == 1
    assert set(snapshot["consumers"]) == {"calendar", "box"}

    first.stop()
    assert manager.snapshot()["subscriptions"] == 1
    second.stop()


def test_strategy_feed_reuses_existing_canonical_descriptor_for_shared_token():
    manager = CommonWebSocketManager(socket_factory=FakeSocket)
    canonical = InstrumentDescriptor(
        InstrumentKey("NSE", "NSE", "25"),
        "NIFTY",
        "index",
        "NSE",
        "NSE",
    )
    manager.registry.register(canonical)

    feed = CommonStrategyMarketFeed("synthetic-underlyings", manager=manager)
    duplicate = feed.descriptor(
        exchange="NSE",
        token="25",
        symbol="NIFTY",
        instrument_type="index",
        segment="NSE",
        expiry="2026-10-29",
        strike=25000.0,
    )
    received = []
    feed.start([duplicate], received.append)

    assert manager.registry.get(duplicate.key) == canonical
    assert manager.snapshot()["subscriptions"] == 1
    assert manager.snapshot()["consumers"] == ["synthetic-underlyings"]

    feed.stop()
