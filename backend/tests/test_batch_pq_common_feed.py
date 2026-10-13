from datetime import date
from types import SimpleNamespace

from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data import common_strategy_feed
from app.market_data.live_calendar_spread_stream import LiveCalendarSpreadOneSecondCollector
from app.market_data.live_synthetic_stream import (
    LiveSyntheticOptionFutureRecorder,
    SyntheticSubscription,
)


class FakeSocket:
    instances = []

    def __init__(self):
        self.closed = False
        self.connect_calls = []
        self.subscribe_calls = []
        self.unsubscribe_calls = []
        FakeSocket.instances.append(self)

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)

    def subscribe(self, tokens, mode=None):
        self.subscribe_calls.append((list(tokens), mode))

    def subscribe_groups(self, groups, mode=None):
        self.subscribe_calls.append(
            ([token for tokens in groups.values() for token in tokens], mode)
        )

    def unsubscribe_groups(self, groups):
        self.unsubscribe_calls.append(
            [token for tokens in groups.values() for token in tokens]
        )

    def close(self):
        self.closed = True


def test_calendar_and_box_consumers_share_common_nfo_socket(monkeypatch):
    FakeSocket.instances.clear()
    manager = CommonWebSocketManager(socket_factory=FakeSocket)
    monkeypatch.setattr(common_strategy_feed, "shared_common_manager", lambda *a, **k: manager)

    class Master:
        def download(self):
            return []

    calendar = LiveCalendarSpreadOneSecondCollector(
        "unused",
        instrument_master=Master(),
        auth=object(),
    )
    calendar_feed = common_strategy_feed.CommonStrategyMarketFeed(
        "calendar-spread", auth=object()
    )
    calendar_desc = calendar._descriptor({
        "exchange": "NFO",
        "kind": "STOCK_FUTURE",
        "expiry": date(2026, 10, 29),
        "token": "FUT-1",
        "symbol": "ABC29OCT26FUT",
        "underlying": "ABC",
        "lot_size": 100,
    })

    recorder = LiveSyntheticOptionFutureRecorder(
        "unused",
        [SyntheticSubscription(2, "OPT-1", "ABC29OCT26CE", "ABC", "STOCK",
                               "29OCT2026", "CE", 100.0, 100)],
        auth=object(),
    )
    box_feed = common_strategy_feed.CommonStrategyMarketFeed(
        "synthetic-options", auth=object()
    )
    box_desc = recorder._descriptors()[0]

    calendar_received = []
    box_received = []
    calendar_feed.start([calendar_desc], calendar_received.append)
    box_feed.start([box_desc], box_received.append)

    assert len(FakeSocket.instances) == 1
    socket = FakeSocket.instances[0]
    assert socket.connect_calls[0]["mode"] == 3
    assert set(socket.connect_calls[0]["subscriptions"][2]) == {"FUT-1"}
    subscribed = [token for tokens, _mode in socket.subscribe_calls for token in tokens]
    assert set(subscribed) == {"OPT-1"}

    # Exercise the real manager routing/fan-out path with deterministic
    # normalized records. Each strategy consumer must receive its own tick.
    manager._normalizer.normalize = lambda descriptor, _message: SimpleNamespace(
        instrument=descriptor.key,
        symbol=descriptor.symbol,
        timestamp_ns=123456789,
        ltp=101.5,
        bid=101.4,
        ask=101.6,
        as_dict=lambda: {
            "exchange": descriptor.key.exchange,
            "segment": descriptor.key.segment,
            "token": descriptor.key.token,
            "symbol": descriptor.symbol,
            "timestamp_ns": 123456789,
            "ltp": 101.5,
            "bid": 101.4,
            "ask": 101.6,
            "payload": {},
        },
    )
    for expected_token, expected_delivery in (
        ("FUT-1", calendar_received),
        ("OPT-1", box_received),
    ):
        route = next(
            (group, exchange_type, token)
            for group, routes in manager._route_index.items()
            for exchange_type, token in routes
            if token == expected_token
        )
        group, exchange_type, token = route
        manager._on_data(
            group,
            {"token": token, "exchange_type": exchange_type},
            generation=manager._socket_generation[group],
        )
        assert len(expected_delivery) == 1
        assert expected_delivery[0].instrument.token == expected_token
        assert expected_delivery[0].ltp == 101.5

    box_feed.stop()
    calendar_feed.stop()
    assert socket.closed is True
