from datetime import date
from types import SimpleNamespace

from app.market_data.contracts import InstrumentKey, OptionType
from app.scanner.iv_skew_flip.feed import IVSkewFlipFeedAdapter
from app.scanner.iv_skew_flip.universe import resolve_option_universe


def _universe():
    rows = []
    for strike in range(21800, 22101, 50):
        for side in ("CE", "PE"):
            rows.append({
                "exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY",
                "expiry": "12Nov2026", "symbol": f"NIFTY12NOV26{int(strike)}{side}",
                "strike": str(strike * 100), "token": f"{strike}-{side}", "lotsize": "75",
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
