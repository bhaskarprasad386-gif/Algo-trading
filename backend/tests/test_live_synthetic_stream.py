from datetime import datetime
from zoneinfo import ZoneInfo

from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.daily_shard_catalog import DailyMarketDataShardCatalog
from app.market_data.live_synthetic_stream import (
    LiveSyntheticOptionFutureRecorder,
    SyntheticSubscription,
)


def _collector():
    return LiveSyntheticOptionFutureRecorder(
        ":memory:",
        [SyntheticSubscription(
            2, "101", "NIFTY30SEP26CE", "NIFTY", "INDEX",
            "30SEP2026", "CE", 100.0, 75,
        )],
    )


def test_synthetic_subscription_rejects_invalid_instrument_class():
    try:
        LiveSyntheticOptionFutureRecorder(
            ":memory:",
            [SyntheticSubscription(2, "1", "NIFTYFUT", "NIFTY", "OTHER")],
        )
    except ValueError as exc:
        assert "STOCK or INDEX" in str(exc)
    else:
        raise AssertionError("expected invalid instrument class rejection")


def test_synthetic_subscription_deduplicates_exchange_token():
    subscriptions = [
        SyntheticSubscription(2, "1", "NIFTYFUT", "NIFTY", "INDEX"),
        SyntheticSubscription(2, "1", "NIFTYFUT", "NIFTY", "INDEX"),
        SyntheticSubscription(2, "2", "NIFTYCE", "NIFTY", "INDEX", "2026-12-31", "CE", 25000, 75),
    ]
    collector = LiveSyntheticOptionFutureRecorder(":memory:", subscriptions)
    assert len(collector.subscriptions) == 2


def test_market_open_excludes_weekends_and_outside_session():
    ist = ZoneInfo("Asia/Kolkata")
    assert LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 10, 0, tzinfo=ist))
    assert not LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 27, 10, 0, tzinfo=ist))
    assert not LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 9, 0, tzinfo=ist))


def test_normalize_converts_ltp_and_best_bid_ask_without_fabrication():
    collector = _collector()
    meta = collector.subscriptions[0]
    payload = collector._normalize(
        {
            "token": "101",
            "last_traded_price": "12345",
            "best_5_buy_data": [{"price": "12340", "quantity": "10"}],
            "best_5_sell_data": [{"price": "12350", "quantity": "20"}],
        },
        meta,
    )
    assert payload["ltp"] == 123.45
    assert payload["bid"] == 123.40
    assert payload["ask"] == 123.50
    assert payload["bid_qty"] == 10.0
    assert payload["ask_qty"] == 20.0
    assert payload["strike"] == 100.0
    assert payload["lot_size"] == 75


def test_normalize_rejects_invalid_or_non_positive_quote_levels_to_none():
    collector = _collector()
    meta = collector.subscriptions[0]
    payload = collector._normalize(
        {
            "token": "101",
            "last_traded_price": "bad",
            "best_5_buy_data": [{"price": "0", "quantity": "0"}],
            "best_5_sell_data": [{"price": "-1", "quantity": "x"}],
        },
        meta,
    )
    assert payload["ltp"] is None
    assert payload["bid"] is None
    assert payload["ask"] is None
    assert payload["bid_qty"] is None
    assert payload["ask_qty"] is None


def test_stream_persists_previous_second_when_bucket_advances_and_flushes_final_bucket(tmp_path, monkeypatch):
    from app.market_data import live_synthetic_stream as stream

    collector = LiveSyntheticOptionFutureRecorder(
        str(tmp_path / "synthetic.db"),
        [SyntheticSubscription(
            2, "101", "NIFTY30SEP26CE", "NIFTY", "INDEX",
            "30SEP2026", "CE", 100.0, 75,
        )],
        batch_size=1,
        poll_seconds=0.05,
    )

    class FakeAuth:
        def login(self):
            return None

    observed = []

    def on_observation(payload):
        observed.append(payload["source_timestamp_ns"])
        collector.stop_event.set()

    collector.auth = FakeAuth()
    collector.on_observation = on_observation

    class FakeSocket:
        def __init__(self, *, auth):
            self.auth = auth

        def connect(self, **kwargs):
            on_data = kwargs["on_data"]
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": "1750000000000000000",
                "last_traded_price": "12345",
            })
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": "1750000001000000000",
                "last_traded_price": "12355",
            })

        def close(self):
            return None

    collector._manager = CommonWebSocketManager(
        socket_factory=lambda: FakeSocket(auth=collector.auth)
    )
    monkeypatch.setattr(stream, "MarketDataWebSocket", FakeSocket)
    monkeypatch.setattr(
        stream.LiveSyntheticOptionFutureRecorder,
        "market_open",
        staticmethod(lambda now=None: True),
    )

    assert collector._run_session() == 2
    assert observed == [1_750_000_000_000_000_000, 1_750_000_001_000_000_000]

    from app.backtesting.historical_catalog import HistoricalCatalog

    catalog = DailyMarketDataShardCatalog(str(tmp_path / "synthetic.db"))
    try:
        records = catalog.records(
            source=collector.SOURCE,
            instrument="NIFTY30SEP26CE|101",
            timeframe="1s",
        )
    finally:
        catalog.close()

    assert [record.timestamp_ns for record in records] == [1_750_000_000_000_000_000, 1_750_000_001_000_000_000]
    assert [record.payload["ltp"] for record in records] == [123.45, 123.55]
    assert [record.payload["source_timestamp_ns"] for record in records] == [
        1_750_000_000_000_000_000,
        1_750_000_001_000_000_000,
    ]


def test_stream_keeps_second_buckets_independent_per_contract(tmp_path, monkeypatch):
    from app.market_data import live_synthetic_stream as stream

    collector = LiveSyntheticOptionFutureRecorder(
        str(tmp_path / "synthetic.db"),
        [
            SyntheticSubscription(2, "101", "NIFTY30SEP26CE", "NIFTY", "INDEX", "30SEP2026", "CE", 100.0, 75),
            SyntheticSubscription(2, "102", "NIFTY30SEP26PE", "NIFTY", "INDEX", "30SEP2026", "PE", 100.0, 75),
        ],
        batch_size=2,
        poll_seconds=0.05,
    )

    class FakeAuth:
        def login(self):
            return None

    observed = []

    def on_observation(payload):
        observed.append((payload["token"], payload["source_timestamp_ns"]))
        if len(observed) == 2:
            collector.stop_event.set()

    collector.auth = FakeAuth()
    collector.on_observation = on_observation

    class FakeSocket:
        def __init__(self, *, auth):
            self.auth = auth

        def connect(self, **kwargs):
            on_data = kwargs["on_data"]
            for token, symbol, price in (
                ("101", "NIFTY30SEP26CE", "12345"),
                ("102", "NIFTY30SEP26PE", "22345"),
            ):
                on_data({
                    "token": token,
                    "symbol": symbol,
                    "exchange_timestamp": "1750000000000000000",
                    "last_traded_price": price,
                })
            for token, symbol, price in (
                ("101", "NIFTY30SEP26CE", "12355"),
                ("102", "NIFTY30SEP26PE", "22355"),
            ):
                on_data({
                    "token": token,
                    "symbol": symbol,
                    "exchange_timestamp": "1750000001000000000",
                    "last_traded_price": price,
                })

        def close(self):
            return None

    collector._manager = CommonWebSocketManager(
        socket_factory=lambda: FakeSocket(auth=collector.auth)
    )
    monkeypatch.setattr(stream, "MarketDataWebSocket", FakeSocket)
    monkeypatch.setattr(
        stream.LiveSyntheticOptionFutureRecorder,
        "market_open",
        staticmethod(lambda now=None: True),
    )

    assert collector._run_session() == 4
    assert observed == [
        ("101", 1_750_000_000_000_000_000),
        ("102", 1_750_000_000_000_000_000),
        ("101", 1_750_000_001_000_000_000),
        ("102", 1_750_000_001_000_000_000),
    ]

    from app.backtesting.historical_catalog import HistoricalCatalog

    catalog = DailyMarketDataShardCatalog(str(tmp_path / "synthetic.db"))
    try:
        ce = catalog.records(
            source=collector.SOURCE,
            instrument="NIFTY30SEP26CE|101",
            timeframe="1s",
        )
        pe = catalog.records(
            source=collector.SOURCE,
            instrument="NIFTY30SEP26PE|102",
            timeframe="1s",
        )
    finally:
        catalog.close()

    assert [record.timestamp_ns for record in ce] == [
        1_750_000_000_000_000_000,
        1_750_000_001_000_000_000,
    ]
    assert [record.timestamp_ns for record in pe] == [
        1_750_000_000_000_000_000,
        1_750_000_001_000_000_000,
    ]
    assert [record.payload["ltp"] for record in ce] == [123.45, 123.55]
    assert [record.payload["ltp"] for record in pe] == [223.45, 223.55]


def test_stream_keeps_latest_tick_within_the_same_second(tmp_path, monkeypatch):
    from app.market_data import live_synthetic_stream as stream

    collector = LiveSyntheticOptionFutureRecorder(
        str(tmp_path / "synthetic.db"),
        [SyntheticSubscription(
            2, "101", "NIFTY30SEP26CE", "NIFTY", "INDEX",
            "30SEP2026", "CE", 100.0, 75,
        )],
        batch_size=1,
        poll_seconds=0.05,
    )

    class FakeAuth:
        def login(self):
            return None

    collector.auth = FakeAuth()
    observations = []

    def on_observation(payload):
        observations.append(payload["ltp"])
        if len(observations) == 3:
            collector.stop_event.set()

    collector.on_observation = on_observation

    class FakeSocket:
        def __init__(self, *, auth):
            self.auth = auth

        def connect(self, **kwargs):
            on_data = kwargs["on_data"]
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": "1750000000000000000",
                "last_traded_price": "12345",
            })
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": "1750000000900000000",
                "last_traded_price": "12456",
            })
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": "1750000001000000000",
                "last_traded_price": "12567",
            })
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": "1750000002000000000",
                "last_traded_price": "12678",
            })
            collector.stop_event.set()

        def close(self):
            return None

    collector._manager = CommonWebSocketManager(
        socket_factory=lambda: FakeSocket(auth=collector.auth)
    )
    monkeypatch.setattr(stream, "MarketDataWebSocket", FakeSocket)
    monkeypatch.setattr(
        stream.LiveSyntheticOptionFutureRecorder,
        "market_open",
        staticmethod(lambda now=None: True),
    )

    assert collector._run_session() == 3
    assert observations == [124.56, 125.67, 126.78]

    from app.backtesting.historical_catalog import HistoricalCatalog

    catalog = DailyMarketDataShardCatalog(str(tmp_path / "synthetic.db"))
    try:
        records = catalog.records(
            source=collector.SOURCE,
            instrument="NIFTY30SEP26CE|101",
            timeframe="1s",
        )
    finally:
        catalog.close()

    assert [record.timestamp_ns for record in records] == [
        1_750_000_000_900_000_000,
        1_750_000_001_000_000_000,
        1_750_000_002_000_000_000,
    ]
    assert [record.payload["ltp"] for record in records] == [124.56, 125.67, 126.78]


def test_stream_ignores_unsubscribed_tokens_without_persisting(tmp_path, monkeypatch):
    from app.market_data import live_synthetic_stream as stream

    collector = LiveSyntheticOptionFutureRecorder(
        str(tmp_path / "synthetic.db"),
        [SyntheticSubscription(
            2, "101", "NIFTY30SEP26CE", "NIFTY", "INDEX",
            "30SEP2026", "CE", 100.0, 75,
        )],
        batch_size=1,
        poll_seconds=0.05,
    )

    class FakeAuth:
        def login(self):
            return None

    collector.auth = FakeAuth()

    class FakeSocket:
        def __init__(self, *, auth):
            self.auth = auth

        def connect(self, **kwargs):
            on_data = kwargs["on_data"]
            on_data({
                "token": "999",
                "symbol": "UNSUBSCRIBED",
                "exchange_timestamp": "1750000000000000000",
                "last_traded_price": "99999",
            })
            collector.stop_event.set()

        def close(self):
            return None

    collector._manager = CommonWebSocketManager(
        socket_factory=lambda: FakeSocket(auth=collector.auth)
    )
    monkeypatch.setattr(stream, "MarketDataWebSocket", FakeSocket)
    monkeypatch.setattr(
        stream.LiveSyntheticOptionFutureRecorder,
        "market_open",
        staticmethod(lambda now=None: True),
    )

    assert collector._run_session() == 0

    from app.backtesting.historical_catalog import HistoricalCatalog

    catalog = DailyMarketDataShardCatalog(str(tmp_path / "synthetic.db"))
    try:
        records = catalog.records(
            source=collector.SOURCE,
            instrument="UNSUBSCRIBED|999",
            timeframe="1s",
        )
    finally:
        catalog.close()

    assert not records

def test_market_open_keeps_equity_derivatives_open_until_1540():
    ist = ZoneInfo("Asia/Kolkata")
    assert LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 15, 39, 59, tzinfo=ist))
    assert LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 15, 40, 0, tzinfo=ist))
    assert not LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 15, 40, 1, tzinfo=ist))
