from datetime import date

from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data import common_strategy_feed
from app.market_data.shared_cache import get_shared_market_data_cache
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
    cache = get_shared_market_data_cache()
    cache.clear()
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

    cache.put(calendar_desc.key.value, {
        "exchange": "NFO", "segment": "NFO", "token": "FUT-1",
        "symbol": calendar_desc.symbol, "instrument_type": "future",
        "timestamp_ns": 1000, "ltp": 101.0, "payload": {"source": "cache"},
    }, timestamp_ns=1000)
    cache.put(box_desc.key.value, {
        "exchange": "NFO", "segment": "NFO", "token": "OPT-1",
        "symbol": box_desc.symbol, "instrument_type": "option",
        "timestamp_ns": 2000, "ltp": 202.0, "strike": 100.0,
        "option_type": "CE", "payload": {"source": "cache"},
    }, timestamp_ns=2000)
    calendar_records = []
    box_records = []
    calendar_feed.start([calendar_desc], calendar_records.append)
    box_feed.start([box_desc], box_records.append)

    assert [(r.timestamp_ns, r.ltp) for r in calendar_records] == [(1000, 101.0)]
    assert [(r.timestamp_ns, r.ltp) for r in box_records] == [(2000, 202.0)]

    assert len(FakeSocket.instances) == 1
    socket = FakeSocket.instances[0]
    assert socket.connect_calls[0]["mode"] == 3
    assert set(socket.connect_calls[0]["subscriptions"][2]) == {"FUT-1"}
    subscribed = [token for tokens, _mode in socket.subscribe_calls for token in tokens]
    assert set(subscribed) == {"OPT-1"}

    box_feed.stop()
    calendar_feed.stop()
    assert socket.closed is True
    cache.clear()
