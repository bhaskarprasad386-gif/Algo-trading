from types import SimpleNamespace

from app.market_data.contracts import InstrumentKey, InstrumentType
from app.market_data.normalizer import AngelOneTickNormalizer
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
    assert adapter.spot_snapshot("NIFTY", now_ns=24_000_000_001) is None
    stale = adapter.snapshot(now_ns=24_000_000_001)
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



def test_spot_feed_default_freshness_accepts_source_age_within_five_seconds():
    feed = FakeFeed()
    now_ns = 15_000_000_000
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: now_ns)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=10_500_000_000,
        ltp=24125.5,
        payload={"exchange_timestamp_ns": 10_500_000_000},
    ))
    assert adapter.spot_snapshot("NIFTY") is not None
    health = adapter.snapshot(now_ns=now_ns)
    assert health["spot"]["NIFTY"]["source_age_ms"] == 4500.0
    assert health["spot"]["NIFTY"]["fresh"] is True


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
    # stop() drains the bounded persistence queue before the test inspects storage.
    adapter.stop()
    assert persisted["NIFTY"] == (24125.5, 10_000_000_000)

    restored = IVSpotFeedAdapter(
        feed=FakeFeed(), clock_ns=lambda: 20_000_000_000, persist_last_price=True,
    )
    restored_snapshot = restored.snapshot(now_ns=20_000_000_000)
    assert restored_snapshot["spot"]["NIFTY"]["price"] == 24125.5
    assert restored_snapshot["spot"]["NIFTY"]["fresh"] is False
    assert restored_snapshot["spot"]["NIFTY"]["quote_status"] == "LAST_KNOWN_STALE"
    assert restored_snapshot["signals_allowed"] is False


def test_spot_freshness_does_not_refresh_receive_time_for_repeated_exchange_timestamp():
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
    assert quote.received_at_ns == 20_000_000_000
    snapshot = adapter.snapshot(now_ns=21_000_000_000)
    assert snapshot["status"] == "PARTIAL"
    assert snapshot["spot"]["NIFTY"]["age_ms"] == 1000.0
    assert snapshot["spot"]["NIFTY"]["source_age_ms"] == 2000.0
    validation = snapshot["record_validation"]
    assert validation["accepted_records"] == 1
    assert validation["duplicate_source_timestamps"] == 1


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


def test_spot_feed_exposes_shared_transport_routing_diagnostics():
    feed = FakeFeed()
    feed.snapshot = lambda: {
        "raw_frames_received": 120,
        "ticks_received": 14,
        "unmatched_frames": 3,
        "exchange_mismatch_frames": 2,
        "normalized_records_delivered": 9,
        "last_successful_delivery_age_seconds_by_group": {"1:0": 0.4},
    }
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(FakeMaster())

    health = adapter.snapshot()
    assert health["expected_instruments"]["NIFTY"] == {
        "exchange": "NSE", "segment": "NSE", "token": "99926000",
    }
    assert health["transport"]["scope"] == "manager_instance"
    assert health["transport"]["raw_frames_received"] == 120
    assert health["transport"]["ticks_received"] == 14
    assert health["transport"]["unmatched_frames"] == 3
    assert health["transport"]["exchange_mismatch_frames"] == 2
    assert health["transport"]["normalized_records_delivered"] == 9
    assert health["transport"]["last_successful_delivery_age_seconds_by_group"] == {"1:0": 0.4}



def test_spot_feed_exposes_actual_common_manager_exchange_freshness_field_names():
    feed = FakeFeed()
    feed.snapshot = lambda: {
        "last_exchange_tick_received_age_seconds_by_exchange_type": {"1": 0.25},
        "last_raw_frame_age_seconds_by_exchange_group": {"1:0:1": 0.1},
    }
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(FakeMaster())

    transport = adapter.snapshot()["transport"]
    assert transport["last_exchange_tick_received_age_seconds_by_exchange_type"] == {"1": 0.25}
    assert transport["last_raw_frame_age_seconds_by_exchange_group"] == {"1:0:1": 0.1}


def test_spot_feed_reports_consumer_accepted_and_rejected_records():
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 20_000_000_000)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")

    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        timestamp_ns=19_000_000_000,
        ltp=24125.5,
        payload={"exchange_timestamp": 19_000},
    ))
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        timestamp_ns=19_500_000_000,
        ltp=24126.0,
        payload={},
    ))
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        timestamp_ns=18_000_000_000,
        ltp=24100.0,
        payload={"exchange_timestamp": 18_000},
    ))

    validation = adapter.snapshot()["record_validation"]
    assert validation["scope"] == "iv_spot_consumer"
    assert validation["callbacks_received"] == 3
    assert validation["accepted_records"] == 1
    assert validation["rejected_records"] == 2
    assert validation["rejected_by_reason"] == {
        "missing_or_invalid_provider_timestamp": 1,
        "out_of_order_source_timestamp": 1,
    }



def test_spot_feed_does_not_accept_repeated_source_timestamp_as_new_tick():
    feed = FakeFeed()
    now = {"value": 20_000_000_000}
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: now["value"])
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    first = SimpleNamespace(
        instrument=nifty.key,
        timestamp_ns=19_000_000_000,
        ltp=24125.5,
        payload={"exchange_timestamp": 19_000},
    )
    feed.callback(first)
    accepted = adapter.last_spot_snapshot("NIFTY")
    assert accepted is not None

    now["value"] += 10_000_000_000
    feed.callback(SimpleNamespace(
        instrument=nifty.key,
        timestamp_ns=19_000_000_000,
        ltp=24199.0,
        payload={"exchange_timestamp": 19_000},
    ))

    current = adapter.last_spot_snapshot("NIFTY")
    health = adapter.snapshot()
    assert current == accepted
    assert current.price == 24125.5
    assert current.received_at_ns == 20_000_000_000
    assert health["record_validation"]["callbacks_received"] == 2
    assert health["record_validation"]["accepted_records"] == 1
    assert health["record_validation"]["duplicate_source_timestamps"] == 1


def test_smartapi_normalizer_to_iv_spot_adapter_preserves_provider_timestamp():
    feed = FakeFeed()
    exchange_timestamp_ms = 1_800_000_000_000
    received_at_ns = exchange_timestamp_ms * 1_000_000 + 500_000_000
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: received_at_ns)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")

    # Model the provider's raw SmartAPI WebSocket tick: LTP is paise-scaled
    # and exchange_timestamp is epoch milliseconds.
    raw_tick = {
        "token": "99926000",
        "exchange_type": 1,
        "exchange_timestamp": str(exchange_timestamp_ms),
        "last_traded_price": 2_412_550,
    }
    record = AngelOneTickNormalizer().normalize(nifty, raw_tick)
    feed.callback(record)

    quote = adapter.spot_snapshot("NIFTY")
    assert quote is not None
    assert quote.timestamp_ns == exchange_timestamp_ms * 1_000_000
    assert quote.received_at_ns == received_at_ns
    health = adapter.snapshot()
    assert health["spot"]["NIFTY"]["price"] == 24125.5
    assert health["spot"]["NIFTY"]["source_age_ms"] == 500.0
    assert health["record_validation"]["callbacks_received"] == 1
    assert health["record_validation"]["accepted_records"] == 1

    
def test_spot_feed_exposes_last_known_spot_for_universe_without_marking_it_fresh():
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: 20_000_000_000)
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    feed.callback(SimpleNamespace(
        instrument=nifty.key, instrument_type=InstrumentType.INDEX,
        timestamp_ns=10_000_000_000, ltp=24125.5,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    assert adapter.spot_snapshot("NIFTY") is None
    last = adapter.last_spot_snapshot("NIFTY")
    assert last is not None
    assert last.price == 24125.5
    assert last.timestamp_ns == 10_000_000_000
    assert adapter.snapshot()["signals_allowed"] is False


def test_repeated_old_source_timestamp_never_refreshes_market_freshness():
    now = [200_000_000_000]
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(feed=feed, clock_ns=lambda: now[0])
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    old_source_ns = now[0] - 100_000_000_000
    record = SimpleNamespace(
        instrument=nifty.key,
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=old_source_ns,
        ltp=25044.65,
        payload={"exchange_timestamp_ns": old_source_ns},
    )

    feed.callback(record)
    now[0] += 1_000_000_000
    feed.callback(record)
    snapshot = adapter.snapshot(now_ns=now[0])

    assert snapshot["record_validation"]["callbacks_received"] == 2
    assert snapshot["record_validation"]["accepted_records"] == 1
    assert snapshot["record_validation"]["duplicate_source_timestamps"] == 1
    # The duplicate frame must not refresh receive-time freshness.
    assert snapshot["spot"]["NIFTY"]["age_ms"] == 1000.0
    assert snapshot["spot"]["NIFTY"]["source_age_ms"] == 101000.0
    assert snapshot["spot"]["NIFTY"]["fresh"] is False
    assert snapshot["spot"]["NIFTY"]["quote_status"] == "LAST_KNOWN_STALE"
    assert snapshot["signals_allowed"] is False
    assert adapter.spot_snapshot("NIFTY", now_ns=now[0]) is None


def test_spot_websocket_callback_never_waits_for_slow_database_persistence(monkeypatch):
    from threading import Event, Thread
    from app.scanner.iv_skew_flip import spot_feed as spot_feed_module

    commit_started = Event()
    allow_commit = Event()

    class BlockingSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def query(self, model):
            return self

        def all(self):
            return []

        def merge(self, row):
            return None

        def commit(self):
            commit_started.set()
            allow_commit.wait(timeout=2.0)

    monkeypatch.setattr(spot_feed_module, "SessionLocal", BlockingSession)
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(
        feed=feed, clock_ns=lambda: 10_000_000_000, persist_last_price=True,
    )
    adapter.start(FakeMaster())
    nifty = next(item for item in feed.descriptors if item.underlying == "NIFTY")
    callback_done = Event()

    def deliver():
        feed.callback(SimpleNamespace(
            instrument=nifty.key,
            instrument_type=InstrumentType.INDEX,
            timestamp_ns=10_000_000_000,
            ltp=24125.5,
            payload={"exchange_timestamp_ns": 10_000_000_000},
        ))
        callback_done.set()

    delivery = Thread(target=deliver, daemon=True)
    delivery.start()
    assert callback_done.wait(timeout=0.25), "shared-feed callback blocked on IV database I/O"
    assert commit_started.wait(timeout=1.0), "IV persistence worker did not start"
    assert adapter.spot_snapshot("NIFTY") is not None

    allow_commit.set()
    delivery.join(timeout=1.0)
    adapter.stop()


def test_spot_persistence_batches_both_indices_in_one_transaction(monkeypatch):
    from app.scanner.iv_skew_flip import spot_feed as spot_feed_module

    sessions = []

    class RecordingSession:
        def __init__(self):
            self.rows = []
            self.commits = 0
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def merge(self, row):
            self.rows.append(row)

        def commit(self):
            self.commits += 1

    monkeypatch.setattr(spot_feed_module, "SessionLocal", RecordingSession)
    feed = FakeFeed()
    adapter = IVSpotFeedAdapter(
        feed=feed, clock_ns=lambda: 10_000_000_000, persist_last_price=True,
    )
    adapter.start(FakeMaster())

    for descriptor, price in (
        (next(item for item in feed.descriptors if item.underlying == "NIFTY"), 24125.5),
        (next(item for item in feed.descriptors if item.underlying == "BANKNIFTY"), 51234.5),
    ):
        feed.callback(SimpleNamespace(
            instrument=descriptor.key,
            instrument_type=InstrumentType.INDEX,
            timestamp_ns=10_000_000_000,
            ltp=price,
            payload={"exchange_timestamp_ns": 10_000_000_000},
        ))

    # Stop drains the bounded pending-symbol map before assertions.
    adapter.stop()

    # Constructor restoration uses one read-only session; the worker must
    # then persist both symbols in exactly one separate write transaction.
    assert len(sessions) == 2
    write_sessions = [session for session in sessions if session.commits]
    assert len(write_sessions) == 1
    assert write_sessions[0].commits == 1
    assert len(write_sessions[0].rows) == 4
    assert {row.symbol for row in write_sessions[0].rows if isinstance(row, spot_feed_module.IVSpotLastPrice)} == {
        "NIFTY", "BANKNIFTY"
    }
    assert {row.underlying for row in write_sessions[0].rows if isinstance(row, spot_feed_module.IVSpotObservation)} == {
        "NIFTY", "BANKNIFTY"
    }
    persistence = adapter.snapshot()["record_validation"]["persistence"]
    assert persistence["batches_committed"] == 1
    assert persistence["observations_committed"] == 2
    assert persistence["failed_batches"] == 0
    assert persistence["pending_symbols"] == 0

def test_idle_spot_persistence_worker_does_not_open_database_session(monkeypatch):
    from threading import Event
    from app.scanner.iv_skew_flip import spot_feed as spot_feed_module

    feed = IVSpotFeedAdapter(feed=FakeFeed(), persist_last_price=False)
    stop = Event()
    feed._persist_stop = stop

    class StopAfterIdleWakeups:
        def __init__(self):
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 2:
                stop.set()
            return True

        def clear(self):
            return None

        def set(self):
            return None

    feed._persist_wakeup = StopAfterIdleWakeups()

    def unexpected_session():
        raise AssertionError("idle worker must not acquire a database session")

    monkeypatch.setattr(spot_feed_module, "SessionLocal", unexpected_session)
    feed._run_persistence()
    assert feed._persist_wakeup.calls == 2
    assert feed._persist_batches == 0
    assert feed._persist_errors == 0

def test_failed_spot_persistence_batch_is_retried(monkeypatch):
    from threading import Event
    from app.scanner.iv_skew_flip import spot_feed as spot_feed_module

    adapter = IVSpotFeedAdapter(feed=FakeFeed(), persist_last_price=False)
    stop = Event()
    adapter._persist_stop = stop
    older = spot_feed_module.SpotSnapshot(100.0, 10_000_000_000, 10_000_000_000)
    newer = spot_feed_module.SpotSnapshot(101.0, 11_000_000_000, 11_000_000_000)
    adapter._persist_pending["NIFTY"] = older

    class ControlledWakeup:
        def __init__(self):
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 3:
                stop.set()
            return True

        def clear(self):
            return None

        def set(self):
            return None

    adapter._persist_wakeup = ControlledWakeup()
    attempts = []
    session_calls = []

    class SuccessfulSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def merge(self, row):
            attempts.append(row)

        def commit(self):
            return None

    def session_factory():
        session_calls.append(True)
        if len(session_calls) == 1:
            # A newer tick arrives while the first DB checkout fails. The
            # retry must persist the newest value, not the stale batch.
            adapter._enqueue_persistence("NIFTY", newer)
            raise RuntimeError("temporary database pressure")
        return SuccessfulSession()

    monkeypatch.setattr(spot_feed_module, "SessionLocal", session_factory)
    adapter._run_persistence()
    assert len(attempts) == 2
    assert attempts[0].price == newer.price
    assert adapter._persist_errors == 1
    assert adapter._persist_batches == 1
    assert adapter._persist_pending == {}



def test_spot_persistence_uses_two_conflict_safe_bulk_statements_without_selects(monkeypatch):
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.core.database import Base
    from app.models.iv_spot_last_price import IVSpotLastPrice
    from app.models.iv_spot_observation import IVSpotObservation
    from app.scanner.iv_skew_flip import spot_feed as spot_feed_module

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[
        IVSpotLastPrice.__table__, IVSpotObservation.__table__,
    ])
    Session = sessionmaker(bind=engine, autoflush=False)
    with Session() as db:
        db.add(IVSpotLastPrice(symbol="NIFTY", price=25000.0, timestamp_ns=20_000_000_000))
        db.commit()
    monkeypatch.setattr(spot_feed_module, "SessionLocal", Session)

    adapter = IVSpotFeedAdapter(
        feed=FakeFeed(), clock_ns=lambda: 30_000_000_000, persist_last_price=True,
    )
    statements = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, executemany))

    event.listen(engine, "before_cursor_execute", capture_sql)
    # The NIFTY retry is older than the durable LAST row and must not regress it.
    # BANKNIFTY is a new observation. Both symbols share one worker transaction.
    adapter._enqueue_persistence(
        "NIFTY", spot_feed_module.SpotSnapshot(24900.0, 10_000_000_000, 30_000_000_000),
    )
    adapter._enqueue_persistence(
        "BANKNIFTY", spot_feed_module.SpotSnapshot(51000.0, 30_000_000_000, 30_000_000_000),
    )
    adapter._ensure_persistence_worker()
    adapter._stop_persistence_worker()

    selects = [sql for sql, _ in statements if sql.lstrip().upper().startswith("SELECT")]
    inserts = [sql for sql, _ in statements if sql.lstrip().upper().startswith("INSERT")]
    assert selects == [], "spot persistence must not issue per-row merge SELECTs"
    assert len(inserts) == 2, "one bulk upsert and one bulk idempotent observation insert"

    with Session() as db:
        nifty = db.query(IVSpotLastPrice).filter_by(symbol="NIFTY").one()
        banknifty = db.query(IVSpotLastPrice).filter_by(symbol="BANKNIFTY").one()
        observations = db.query(IVSpotObservation).all()
        assert (nifty.price, nifty.timestamp_ns) == (25000.0, 20_000_000_000)
        assert (banknifty.price, banknifty.timestamp_ns) == (51000.0, 30_000_000_000)
        assert len(observations) == 2
    assert adapter._persist_batches == 1
    assert adapter._persisted_rows == 2
    assert adapter._persist_errors == 0
    engine.dispose()


def test_spot_persistence_worker_exits_on_shutdown_failure_and_keeps_retry_queue(monkeypatch):
    from app.scanner.iv_skew_flip import spot_feed as spot_feed_module

    adapter = IVSpotFeedAdapter(feed=FakeFeed(), persist_last_price=False)
    snapshot = spot_feed_module.SpotSnapshot(100.0, 10_000_000_000, 10_000_000_000)
    adapter._persist_pending["NIFTY"] = snapshot
    adapter._persist_stop.set()

    class ImmediateWakeup:
        def wait(self, timeout=None):
            return True

        def clear(self):
            return None

    adapter._persist_wakeup = ImmediateWakeup()

    def unavailable_database():
        raise RuntimeError("database unavailable during shutdown")

    monkeypatch.setattr(spot_feed_module, "SessionLocal", unavailable_database)
    adapter._run_persistence()

    assert adapter._persist_pending == {"NIFTY": snapshot}
    assert adapter._persist_errors == 1
    assert "database unavailable" in adapter._last_persist_error
