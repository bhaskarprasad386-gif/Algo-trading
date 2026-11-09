from datetime import date
from types import SimpleNamespace

from app.market_data.contracts import InstrumentKey, OptionType
from app.scanner.iv_skew_flip.feed import IVSkewFlipFeedAdapter
from app.scanner.iv_skew_flip.universe import resolve_option_universe


def _universe():
    rows = []
    # Both nearest weekly and monthly expiries are required to classify the
    # nearest date safely; a single-expiry fixture is ambiguous.
    for expiry_index, expiry in enumerate(("12Nov2026", "26Nov2026")):
        expiry_code = "12NOV26" if expiry_index == 0 else "26NOV26"
        for strike in range(21800, 22101, 50):
            for side in ("CE", "PE"):
                rows.append({
                    "exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY",
                    "expiry": expiry, "symbol": f"NIFTY{expiry_code}{int(strike)}{side}",
                    "strike": str(strike * 100), "token": f"{expiry_index}-{strike}-{side}", "lotsize": "75",
                })
    return resolve_option_universe(
        rows, underlying="NIFTY", expiry_type="WEEKLY",
        spot=21950, today=date(2026, 11, 6),
    )

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


def test_feed_adapter_subscribes_exactly_fourteen_contracts_and_retains_symbols():
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 1_000_000_000)
    result = adapter.start(_universe())
    assert result["status"] == "SUBSCRIBED"
    assert result["contract_count"] == 14
    assert len(feed.descriptors) == 14
    assert {item.symbol for item in feed.descriptors} == {
        f"NIFTY12NOV26{int(strike)}{side}"
        for strike in _universe().strikes for side in ("CE", "PE")
    }
    assert {item.key.exchange for item in feed.descriptors} == {"NFO"}


def test_feed_adapter_accepts_only_its_subscribed_contract_and_reports_freshness():
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    record = SimpleNamespace(
        instrument=descriptor.key, option_type=OptionType(descriptor.option_type),
        strike=descriptor.strike, timestamp_ns=10_000_000_000,
        ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    )
    feed.callback(record)
    quotes = adapter.latest_quotes()
    assert len(quotes) == 1
    assert quotes[0].option_type in {"CE", "PE"}
    snapshot = adapter.snapshot(10_000_000_000)
    assert snapshot["received_contracts"] == 1
    assert snapshot["fresh_contracts"] == 1
    assert snapshot["status"] == "PARTIAL"
    assert snapshot["signals_allowed"] is False


def test_feed_adapter_drops_unsubscribed_tokens_and_stale_quotes():
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 20_000_000_000)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    wrong = SimpleNamespace(
        instrument=InstrumentKey("NFO", "NFO", "not-subscribed"),
        option_type=OptionType(descriptor.option_type), strike=descriptor.strike,
        timestamp_ns=20_000_000_000, ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10,
    )
    feed.callback(wrong)
    assert adapter.latest_quotes() == ()
    valid = SimpleNamespace(
        instrument=descriptor.key, option_type=OptionType(descriptor.option_type),
        strike=descriptor.strike, timestamp_ns=10_000_000_000,
        ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    )
    feed.callback(valid)
    snapshot = adapter.snapshot(20_000_000_000)
    assert snapshot["status"] == "STALE"
    assert snapshot["signals_allowed"] is False


def test_stopping_adapter_releases_only_its_feed_consumer():
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed)
    adapter.start(_universe())
    adapter.stop()
    assert feed.stop_calls == 2  # old-universe handoff + explicit stop
    assert adapter.snapshot(1)["status"] == "STOPPED"



def test_feed_adapter_rejects_local_clock_fallback_without_provider_timestamp():
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    fallback = SimpleNamespace(
        instrument=descriptor.key, option_type=OptionType(descriptor.option_type),
        strike=descriptor.strike, timestamp_ns=10_000_000_000,
        ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10, payload={},
    )
    feed.callback(fallback)
    assert adapter.latest_quotes() == ()
    assert adapter.snapshot(10_000_000_000)["status"] == "WAITING_FOR_TICKS"


import pytest


@pytest.mark.parametrize("timestamp_field", [
    "exchange_timestamp_ns",
    "timestamp_ns",
    "exchange_timestamp",
    "exchange_timestamp_ms",
    "timestamp",
    "feed_time",
])
def test_feed_adapter_accepts_each_supported_provider_timestamp_alias(timestamp_field):
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    feed.callback(SimpleNamespace(
        instrument=descriptor.key,
        option_type=OptionType(descriptor.option_type),
        strike=descriptor.strike,
        timestamp_ns=10_000_000_000,
        ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10,
        payload={timestamp_field: 10_000_000_000},
    ))
    assert len(adapter.latest_quotes()) == 1


@pytest.mark.parametrize("timestamp_field", [
    "exchange_timestamp_ns", "timestamp_ns", "exchange_timestamp",
    "exchange_timestamp_ms", "timestamp", "feed_time",
])
@pytest.mark.parametrize("invalid_value", [None, 0, -1, "", "not-a-timestamp", True])
def test_feed_adapter_rejects_invalid_provider_timestamp_alias_values(timestamp_field, invalid_value):
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    feed.callback(SimpleNamespace(
        instrument=descriptor.key,
        option_type=OptionType(descriptor.option_type),
        strike=descriptor.strike,
        timestamp_ns=10_000_000_000,
        ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10,
        payload={timestamp_field: invalid_value},
    ))
    assert adapter.latest_quotes() == ()


@pytest.mark.parametrize("timestamp_ns", [True, False, 0, -1])
def test_feed_adapter_rejects_invalid_normalized_provider_timestamp(timestamp_ns):
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    feed.callback(SimpleNamespace(
        instrument=descriptor.key,
        option_type=OptionType(descriptor.option_type),
        strike=descriptor.strike,
        timestamp_ns=timestamp_ns,
        ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    assert adapter.latest_quotes() == ()


@pytest.mark.parametrize("invalid_value", ["inf", "-inf", float("inf"), float("-inf"), float("nan")])
def test_feed_adapter_rejects_non_finite_provider_timestamp(invalid_value):
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    feed.callback(SimpleNamespace(
        instrument=descriptor.key,
        option_type=OptionType(descriptor.option_type),
        strike=descriptor.strike,
        timestamp_ns=10_000_000_000,
        ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10,
        payload={"exchange_timestamp_ns": invalid_value},
    ))
    assert adapter.latest_quotes() == ()



def test_feed_snapshot_exposes_live_option_prices_depth_oi_and_source_age():
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 10_500_000_000)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    feed.callback(SimpleNamespace(
        instrument=descriptor.key,
        option_type=OptionType(descriptor.option_type),
        strike=descriptor.strike,
        timestamp_ns=10_000_000_000,
        ltp=100.25,
        bid=99.5,
        bid_qty=120,
        ask=101.0,
        ask_qty=95,
        oi=123456,
        volume=789,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))

    quote = adapter.snapshot(10_500_000_000)["quotes"][0]
    assert quote["symbol"] == descriptor.symbol
    assert quote["token"] == descriptor.key.token
    assert quote["ltp"] == 100.25
    assert quote["bid"] == 99.5
    assert quote["bid_qty"] == 120.0
    assert quote["ask"] == 101.0
    assert quote["ask_qty"] == 95.0
    assert quote["oi"] == 123456
    assert quote["volume"] == 789
    assert quote["age_ms"] == 500.0
    assert quote["fresh"] is True


def test_feed_adapter_uses_its_subscription_metadata_when_shared_record_is_generic():
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    # A shared registry descriptor can carry no option metadata (or metadata
    # belonging to a different consumer); token identity remains authoritative.
    feed.callback(SimpleNamespace(
        instrument=descriptor.key,
        timestamp_ns=10_000_000_000,
        ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    quotes = adapter.latest_quotes()
    assert len(quotes) == 1
    assert quotes[0].strike == descriptor.strike
    assert quotes[0].option_type == descriptor.option_type
    assert quotes[0].token == descriptor.key.token


def test_feed_adapter_exposes_reasons_for_rejected_records():
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    feed.callback(SimpleNamespace(instrument=descriptor.key, timestamp_ns=10_000_000_000, ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10, payload={}))
    feed.callback(SimpleNamespace(instrument=InstrumentKey("NFO", "NFO", "not-subscribed"), timestamp_ns=10_000_000_000, ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10, payload={"exchange_timestamp_ns": 10_000_000_000}))
    feed.callback(SimpleNamespace(instrument=descriptor.key, timestamp_ns=10_000_000_000, ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10, payload={"exchange_timestamp_ns": 10_000_000_000}))
    feed.callback(SimpleNamespace(instrument=descriptor.key, timestamp_ns=9_000_000_000, ltp=100.0, bid=99.0, ask=101.0, oi=1000, volume=10, payload={"exchange_timestamp_ns": 9_000_000_000}))
    snapshot = adapter.snapshot(10_000_000_000)
    assert snapshot["rejected_records"] == {"missing_token": 0, "invalid_timestamp": 0, "missing_provider_timestamp": 1, "unsubscribed_token": 1, "out_of_order_timestamp": 1}
    assert snapshot["rejected_record_count"] == 3
    assert snapshot["last_rejection_reason"] == "out_of_order_timestamp"
    assert snapshot["received_contracts"] == 1

    # A new expiry/universe starts a fresh diagnostic window.
    adapter.start(_universe())
    reset = adapter.snapshot(10_000_000_000)
    assert reset["rejected_record_count"] == 0
    assert reset["last_rejection_reason"] is None


def test_feed_snapshot_includes_shared_socket_delivery_diagnostics():
    feed = FakeFeed()
    feed.snapshot = lambda: {
        "subscriptions": 14,
        "socket_groups": 1,
        "connected_groups": ["3:0"],
        "raw_frames_received": 0,
        "ticks_received": 0,
        "normalized_records_emitted": 0,
        "normalized_records_delivered": 0,
        "unmatched_frames": 0,
        "unconnected_required_groups": [],
    }
    adapter = IVSkewFlipFeedAdapter(feed=feed, clock_ns=lambda: 10_000_000_000)
    adapter.start(_universe())
    snapshot = adapter.snapshot(10_000_000_000)
    assert snapshot["subscribed_token_count"] == 14
    assert snapshot["shared_feed_diagnostics"]["subscriptions"] == 14
    assert snapshot["shared_feed_diagnostics"]["raw_frames_received"] == 0
    assert snapshot["signals_allowed"] is False



def test_feed_preserves_last_oi_on_price_only_frames_and_expires_after_five_minutes():
    feed = FakeFeed()
    adapter = IVSkewFlipFeedAdapter(feed=feed)
    adapter.start(_universe())
    descriptor = feed.descriptors[0]
    feed.callback(SimpleNamespace(
        instrument=descriptor.key, option_type=OptionType(descriptor.option_type),
        strike=descriptor.strike, timestamp_ns=10_000_000_000,
        ltp=100.0, bid=99.0, ask=101.0, oi=12345, volume=10,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    # A newer LTP/depth-only frame must not erase the last broker OI observation.
    feed.callback(SimpleNamespace(
        instrument=descriptor.key, option_type=OptionType(descriptor.option_type),
        strike=descriptor.strike, timestamp_ns=11_000_000_000,
        ltp=101.0, bid=100.0, ask=102.0, oi=None, volume=11,
        payload={"exchange_timestamp_ns": 11_000_000_000},
    ))
    fresh = adapter.snapshot(11_000_000_000)["quotes"][0]
    assert fresh["oi"] == 12345
    assert fresh["oi_timestamp_ns"] == 10_000_000_000
    assert fresh["oi_fresh"] is True
    assert fresh["oi_stale"] is False
    assert fresh["oi_age_ms"] == 1000.0

    five_minutes_plus = adapter.snapshot(310_000_000_001)["quotes"][0]
    assert five_minutes_plus["fresh"] is False
    assert five_minutes_plus["oi"] == 12345
    assert five_minutes_plus["oi_fresh"] is False
    assert five_minutes_plus["oi_stale"] is True
