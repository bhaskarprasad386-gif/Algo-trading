from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from app.core.config import settings

@pytest.fixture(autouse=True)
def enable_persistence_for_legacy_stream_persistence_tests(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_MARKET_DATA_PERSISTENCE_ENABLED", True)

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
        assert "STOCK, INDEX or COMMODITY" in str(exc)
    else:
        raise AssertionError("expected invalid instrument class rejection")


def test_synthetic_subscription_accepts_commodity():
    collector = LiveSyntheticOptionFutureRecorder(
        ":memory:",
        [SyntheticSubscription(5, "MCX1", "GOLD26OCTFUT", "GOLD", "COMMODITY")],
    )
    assert collector.subscriptions[0].instrument_class == "COMMODITY"


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
    from time import time_ns

    # Keep fixture ticks within the recorder's five-second freshness window,
    # even when the CI runner is busy before the fake socket connects.
    base_timestamp = (time_ns() // 1_000_000_000 - 1) * 1_000_000_000 + 50_000_000

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
                "exchange_timestamp": str(base_timestamp),
                "last_traded_price": "12345",
            })
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": str(base_timestamp + 1_000_000_000),
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
    assert observed == [base_timestamp, base_timestamp + 1_000_000_000]

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

    assert [record.timestamp_ns for record in records] == [base_timestamp, base_timestamp + 1_000_000_000]
    assert [record.payload["ltp"] for record in records] == [123.45, 123.55]
    assert [record.payload["source_timestamp_ns"] for record in records] == [
        base_timestamp,
        base_timestamp + 1_000_000_000,
    ]


def test_stream_keeps_second_buckets_independent_per_contract(tmp_path, monkeypatch):
    from app.market_data import live_synthetic_stream as stream
    from time import time_ns

    base_timestamp = (time_ns() // 1_000_000_000 - 3) * 1_000_000_000 + 50_000_000

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
                    "exchange_timestamp": str(base_timestamp),
                    "last_traded_price": price,
                })
            for token, symbol, price in (
                ("101", "NIFTY30SEP26CE", "12355"),
                ("102", "NIFTY30SEP26PE", "22355"),
            ):
                on_data({
                    "token": token,
                    "symbol": symbol,
                    "exchange_timestamp": str(base_timestamp + 1_000_000_000),
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
        ("101", base_timestamp),
        ("102", base_timestamp),
        ("101", base_timestamp + 1_000_000_000),
        ("102", base_timestamp + 1_000_000_000),
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
        base_timestamp,
        base_timestamp + 1_000_000_000,
    ]
    assert [record.timestamp_ns for record in pe] == [
        base_timestamp,
        base_timestamp + 1_000_000_000,
    ]
    assert [record.payload["ltp"] for record in ce] == [123.45, 123.55]
    assert [record.payload["ltp"] for record in pe] == [223.45, 223.55]


def test_stream_keeps_latest_tick_within_the_same_second(tmp_path, monkeypatch):
    from app.market_data import live_synthetic_stream as stream
    from time import time_ns

    base_timestamp = (time_ns() // 1_000_000_000 - 3) * 1_000_000_000 + 50_000_000

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
                "exchange_timestamp": str(base_timestamp),
                "last_traded_price": "12345",
            })
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": str(base_timestamp + 900_000_000),
                "last_traded_price": "12456",
            })
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": str(base_timestamp + 1_000_000_000),
                "last_traded_price": "12567",
            })
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": str(base_timestamp + 2_000_000_000),
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
        base_timestamp + 900_000_000,
        base_timestamp + 1_000_000_000,
        base_timestamp + 2_000_000_000,
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

def test_market_open_closes_at_regular_1530_session_boundary():
    ist = ZoneInfo("Asia/Kolkata")
    assert LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 15, 29, 59, tzinfo=ist))
    assert not LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 15, 30, 0, tzinfo=ist))
    assert not LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 15, 30, 1, tzinfo=ist))



def test_emit_logs_scanner_callback_failure_without_propagating(monkeypatch):
    from types import SimpleNamespace
    from app.market_data import live_synthetic_stream as stream

    collector = _collector()
    errors = []
    monkeypatch.setattr(stream, "app_logger", SimpleNamespace(exception=lambda *args: errors.append(args)))

    def broken_callback(_payload):
        raise RuntimeError("scanner callback failed")

    collector.on_observation = broken_callback
    record = SimpleNamespace(
        as_dict=lambda: {"ltp": 123.45},
        symbol="NIFTY30SEP26CE",
        instrument=SimpleNamespace(exchange="NFO", token="101"),
    )
    collector._emit(record, 1_750_000_000_000_000_000)

    assert len(errors) == 1
    assert "Synthetic scanner observation callback failed" in errors[0][0]


def test_recorder_run_forever_logs_unexpected_session_errors(monkeypatch):
    from types import SimpleNamespace
    from app.market_data import live_synthetic_stream as stream

    collector = _collector()
    errors = []
    monkeypatch.setattr(stream, "app_logger", SimpleNamespace(exception=lambda *args: errors.append(args)))
    monkeypatch.setattr(
        collector,
        "_run_session",
        lambda: (_ for _ in ()).throw(RuntimeError("temporary feed failure")),
    )

    def stop_after_retry(_seconds):
        collector.stop_event.set()

    monkeypatch.setattr(stream, "sleep", stop_after_retry)
    monkeypatch.setattr(
        stream.LiveSyntheticOptionFutureRecorder,
        "market_open",
        staticmethod(lambda now=None: True),
    )
    collector.run_forever()

    assert len(errors) == 1
    assert "Synthetic option/future recorder failed" in errors[0][0]


def test_recorder_flushes_completed_second_without_waiting_for_next_token_tick():
    from types import SimpleNamespace

    collector = _collector()
    observed = []
    collector.on_observation = lambda payload: observed.append(payload["source_timestamp_ns"])
    timestamp = 1_750_000_000_100_000_000
    record = SimpleNamespace(
        timestamp_ns=timestamp,
        symbol="NIFTY30SEP26CE",
        instrument=SimpleNamespace(exchange="NFO", token="101"),
        as_dict=lambda: {"ltp": 123.45, "bid": 123.0, "ask": 123.5},
    )
    key = ("NFO", "101")
    collector._latest[key] = ((timestamp // 1_000_000_000) * 1_000_000_000, record)

    flushed = collector._flush_completed_buckets(now_ns=timestamp + 1_100_000_000)

    assert flushed == 1
    assert observed == [timestamp]
    assert collector._latest == {}
    assert collector.snapshot()["observations_emitted"] == 1
    assert collector.snapshot()["missing_executable_quote"] == 0


def test_recorder_does_not_flush_old_quote_as_live():
    from types import SimpleNamespace

    collector = _collector()
    timestamp = 1_750_000_000_100_000_000
    record = SimpleNamespace(timestamp_ns=timestamp, as_dict=lambda: {"bid": 1.0, "ask": 1.1})
    collector._latest[("NFO", "101")] = ((timestamp // 1_000_000_000) * 1_000_000_000, record)

    assert collector._flush_completed_buckets(now_ns=timestamp + 6_000_000_000) == 0
    assert ("NFO", "101") not in collector._latest
    assert collector.snapshot()["stale_buffer_discards"] == 1


def test_synthetic_fallback_socket_disables_private_reconnect(monkeypatch):
    from app.market_data import live_synthetic_stream as stream

    collector = _collector()
    monkeypatch.setattr(settings, "LIVE_MARKET_DATA_PERSISTENCE_ENABLED", False)
    monkeypatch.setattr(
        stream.LiveSyntheticOptionFutureRecorder,
        "market_open",
        staticmethod(lambda now=None: False),
    )
    captured = {}

    class FakeSocket:
        def __init__(self, *, auth, auto_reconnect=True):
            self.auth = auth
            self.auto_reconnect = auto_reconnect

    class FakeFeed:
        @staticmethod
        def descriptor(**kwargs):
            return kwargs

        def __init__(self, consumer, **kwargs):
            captured.update(kwargs)

        def start(self, descriptors, callback):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(stream, "MarketDataWebSocket", FakeSocket)
    monkeypatch.setattr(stream, "CommonStrategyMarketFeed", FakeFeed)

    collector._run_session()

    assert "manager" not in captured
    socket = captured["socket_factory"]()
    assert socket.auth is collector.auth
    assert socket.auto_reconnect is False
