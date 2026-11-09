from types import SimpleNamespace

from app.market_data.contracts import InstrumentKey, InstrumentType
from app.scanner.iv_skew_flip.spot_feed import IVSpotFeedAdapter


class FakeMaster:
    rows = {
        "NIFTY": {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY"},
        "BANKNIFTY": {"token": "99926009", "symbol": "Nifty Bank", "name": "BANKNIFTY"},
    }

    def resolve_index_instrument(self, symbol, exchange):
        assert exchange == "NSE"
        return self.rows[symbol]


class FakeFeed:
    def __init__(self):
        self.descriptors = ()
        self.callback = None
        self.stop_calls = 0

    def start(self, descriptors, callback):
        self.descriptors = tuple(descriptors)
        self.callback = callback
        return tuple(item.key for item in self.descriptors)

    def stop(self):
        self.stop_calls += 1
        self.callback = None


def test_spot_feed_subscribes_nifty_and_banknifty_on_shared_consumer():
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    result = adapter.start(FakeMaster())
    assert result["status"] == "SUBSCRIBED"
    assert result["symbols"] == ["BANKNIFTY", "NIFTY"]
    assert {item.key.exchange for item in feed.descriptors} == {"NSE"}
    assert {item.instrument_type for item in feed.descriptors} == {"index"}
    assert {item.key.token for item in feed.descriptors} == {"99926000", "99926009"}


def test_spot_feed_exposes_only_fresh_matching_index_ticks():
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key, instrument_type=InstrumentType.INDEX,
        timestamp_ns=10_000_000_000, ltp=24125.5, payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    assert adapter.spot_snapshot("NIFTY") is not None
    assert adapter.spot_snapshot("BANKNIFTY") is None
    health = adapter.snapshot()
    assert health["status"] == "PARTIAL"
    assert health["signals_allowed"] is False
    assert health["spot"]["NIFTY"]["price"] == 24125.5


def test_spot_feed_rejects_out_of_order_and_stale_ticks():
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 20_000_000_000)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key, instrument_type="index",
        timestamp_ns=19_000_000_000, ltp=24000.0, payload={"exchange_timestamp_ns": 19_000_000_000},
    ))
    feed.callback(SimpleNamespace(
        instrument=nifty.key, instrument_type="index",
        timestamp_ns=18_000_000_000, ltp=23900.0, payload={"exchange_timestamp_ns": 18_000_000_000},
    ))
    assert adapter.spot_snapshot("NIFTY").price == 24000.0
    assert adapter.spot_snapshot("NIFTY", now_ns=23_000_000_001) is None
    stale = adapter.snapshot(now_ns=23_000_000_001)
    assert stale["status"] == "STALE"
    assert stale["signals_allowed"] is False
    assert stale["spot"]["NIFTY"]["price"] == 24000.0
    assert stale["spot"]["NIFTY"]["last_price"] == 24000.0
    assert stale["spot"]["NIFTY"]["fresh"] is False
    assert stale["spot"]["NIFTY"]["quote_status"] == "LAST_KNOWN_STALE"


def test_spot_feed_does_not_accept_unsubscribed_or_non_index_records():
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(FakeMaster())
    descriptor = feed.descriptors[0]
    feed.callback(SimpleNamespace(
        instrument=InstrumentKey("NSE", "NSE", "other-token"),
        instrument_type="index", timestamp_ns=10_000_000_000, ltp=100.0, payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    feed.callback(SimpleNamespace(
        instrument=descriptor.key, instrument_type="equity",
        timestamp_ns=10_000_000_000, ltp=100.0,
    ))
    feed.callback(SimpleNamespace(
        instrument=descriptor.key, instrument_type="index",
        timestamp_ns=10_000_000_000, ltp=100.0, payload={},
    ))
    assert adapter.snapshot()["status"] == "WAITING_FOR_TICKS"


def test_spot_feed_rejects_duplicate_tokens_and_unsupported_symbols():
    class BadMaster(FakeMaster):
        rows = {
            "NIFTY": {"token": "99926000", "symbol": "Nifty 50"},
            "BANKNIFTY": {"token": "99926000", "symbol": "Nifty Bank"},
        }

    adapter = IVSpotFeedAdapter(feed=FakeFeed())
    try:
        adapter.start(BadMaster())
    except LookupError:
        pass
    else:
        raise AssertionError("duplicate index tokens must fail closed")

    try:
        IVSpotFeedAdapter._descriptors(FakeMaster(), ["SENSEX"])
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported spot symbol must fail closed")


import pytest


@pytest.mark.parametrize("timestamp_field", [
    "exchange_timestamp_ns",
    "timestamp_ns",
    "exchange_timestamp",
    "exchange_timestamp_ms",
    "timestamp",
    "feed_time",
])
def test_spot_feed_accepts_each_supported_provider_timestamp_alias(timestamp_field):
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=10_000_000_000,
        ltp=24125.5,
        payload={timestamp_field: 10_000_000_000},
    ))
    assert adapter.spot_snapshot("NIFTY") is not None


@pytest.mark.parametrize("timestamp_field", [
    "exchange_timestamp_ns", "timestamp_ns", "exchange_timestamp",
    "exchange_timestamp_ms", "timestamp", "feed_time",
])
@pytest.mark.parametrize("invalid_value", [None, 0, -1, "", "not-a-timestamp", True])
def test_spot_feed_rejects_invalid_provider_timestamp_alias_values(timestamp_field, invalid_value):
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=10_000_000_000,
        ltp=24125.5,
        payload={timestamp_field: invalid_value},
    ))
    assert adapter.spot_snapshot("NIFTY") is None


@pytest.mark.parametrize("invalid_value", ["inf", "-inf", float("inf"), float("-inf"), float("nan")])
def test_spot_feed_rejects_non_finite_provider_timestamp(invalid_value):
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=10_000_000_000,
        ltp=24125.5,
        payload={"exchange_timestamp_ns": invalid_value},
    ))
    assert adapter.spot_snapshot("NIFTY") is None


@pytest.mark.parametrize("invalid_price", [float("nan"), float("inf"), float("-inf")])
def test_spot_feed_rejects_non_finite_index_prices(invalid_price):
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=10_000_000_000,
        ltp=invalid_price,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    assert adapter.spot_snapshot("NIFTY") is None



def test_spot_feed_snapshot_exposes_live_price_source_age():
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_500_000_000)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=10_000_000_000,
        ltp=24125.5,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))

    snapshot = adapter.snapshot(now_ns=10_500_000_000)
    assert snapshot["spot"]["NIFTY"]["price"] == 24125.5
    # Receive age starts when the adapter handles the frame; source age keeps
    # the provider timestamp visible independently.
    assert snapshot["spot"]["NIFTY"]["age_ms"] == 0.0
    assert snapshot["spot"]["NIFTY"]["source_age_ms"] == 500.0
    assert snapshot["spot"]["NIFTY"]["fresh"] is True


def test_spot_feed_uses_its_token_subscription_when_shared_record_metadata_is_generic():
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    # A shared registry descriptor can be generic or belong to another consumer.
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        timestamp_ns=10_000_000_000,
        ltp=24125.5,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    assert adapter.spot_snapshot("NIFTY") is not None
    assert adapter.spot_snapshot("NIFTY").price == 24125.5


def test_spot_feed_persists_and_restores_last_known_price(monkeypatch):
    from app.scanner.iv_skew_flip import spot_feed as spot_feed_module

    persisted = {}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def query(self, model):
            assert model is spot_feed_module.IVSpotLastPrice
            return self

        def all(self):
            return [
                SimpleNamespace(symbol=symbol, price=price, timestamp_ns=timestamp_ns)
                for symbol, (price, timestamp_ns) in persisted.items()
            ]

        def merge(self, row):
            persisted[row.symbol] = (row.price, row.timestamp_ns)

        def commit(self):
            return None

    monkeypatch.setattr(spot_feed_module, "SessionLocal", FakeSession)

    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(
        feed=feed, clock_ns=lambda: 10_000_000_000, persist_last_price=True,
    )
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=10_000_000_000,
        ltp=24125.5,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    assert persisted["NIFTY"] == (24125.5, 10_000_000_000)

    adapter.stop()
    restored = IVSpotFeedAdapter(
        feed=FakeFeed(), clock_ns=lambda: 20_000_000_000, persist_last_price=True,
    )
    restored_snapshot = restored.snapshot(now_ns=20_000_000_000)
    assert restored_snapshot["spot"]["NIFTY"]["price"] == 24125.5
    assert restored_snapshot["spot"]["NIFTY"]["fresh"] is False
    assert restored_snapshot["spot"]["NIFTY"]["quote_status"] == "LAST_KNOWN_STALE"
    assert restored_snapshot["signals_allowed"] is False


def test_spot_freshness_uses_recent_receive_time_not_repeated_exchange_timestamp():
    current = {"ns": 20_000_000_000}
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: current["ns"])
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")

    # Broker repeats the same exchange timestamp on a subsequent valid frame.
    feed.callback(SimpleNamespace(
        instrument=nifty.key, instrument_type=InstrumentType.INDEX,
        timestamp_ns=19_000_000_000, ltp=24125.5,
        payload={"exchange_timestamp_ns": 19_000_000_000},
    ))
    current["ns"] = 21_000_000_000
    feed.callback(SimpleNamespace(
        instrument=nifty.key, instrument_type=InstrumentType.INDEX,
        timestamp_ns=19_000_000_000, ltp=24125.5,
        payload={"exchange_timestamp_ns": 19_000_000_000},
    ))

    quote = adapter.spot_snapshot("NIFTY", now_ns=21_000_000_000)
    assert quote is not None
    assert quote.timestamp_ns == 19_000_000_000
    assert quote.received_at_ns == 21_000_000_000
    snapshot = adapter.snapshot(now_ns=21_000_000_000)
    assert snapshot["status"] == "PARTIAL"
    assert snapshot["spot"]["NIFTY"]["age_ms"] == 0.0
    assert snapshot["spot"]["NIFTY"]["source_age_ms"] == 2000.0


def test_spot_feed_rejects_old_source_timestamp_despite_recent_receive():
    current = {"ns": 20_000_000_000}
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: current["ns"])
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key, instrument_type=InstrumentType.INDEX,
        timestamp_ns=10_000_000_000, ltp=24125.5,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    # A repeated old market observation arriving now cannot refresh signal age.
    quote = adapter.spot_snapshot("NIFTY", now_ns=current["ns"])
    assert quote is None
    health = adapter.snapshot(now_ns=current["ns"])
    assert health["signals_allowed"] is False
    assert health["spot"]["NIFTY"]["fresh"] is False
