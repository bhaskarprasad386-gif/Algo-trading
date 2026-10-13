from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker
from app.market_data.live_synthetic_underlying import LiveSyntheticUnderlyingFeed


class _CacheReader:
    def __init__(self, snapshots):
        self.snapshots = snapshots

    def latest_snapshot(self, instrument):
        return self.snapshots.get(instrument.value)


def _descriptor():
    return CommonStrategyMarketFeed.descriptor(
        exchange="NSE",
        token="26000",
        symbol="NIFTY",
        instrument_type="index",
        segment="NSE",
    )


def _underlying_feed(on_price=None):
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (22000.0, 22100.0, 22200.0)})
    feed = LiveSyntheticUnderlyingFeed(
        ("NIFTY",),
        tracker=tracker,
        concrete_tokens={"NIFTY": "26000"},
        index_symbols=frozenset({"NIFTY"}),
        on_price=on_price,
    )
    return feed, tracker


def test_late_starting_underlying_feed_seeds_tracker_from_shared_cache(monkeypatch):
    from app.market_data import live_synthetic_underlying as underlying
    now = 1_800_000_000_100_000_000
    monkeypatch.setattr(underlying, "time_ns", lambda: now)
    descriptor = _descriptor()
    seen = []
    feed, tracker = _underlying_feed(lambda *args: seen.append(args))
    feed._feed = _CacheReader({
        descriptor.key.value: {
            "exchange": "NSE",
            "segment": "NSE",
            "token": "26000",
            "symbol": "NIFTY",
            "timestamp_ns": 1_800_000_000_000_000_000,
            "ltp": 22048.0,
        }
    })

    assert feed._seed_from_cache((descriptor,)) == 1
    assert tracker.atm("NIFTY") == 22000.0
    assert seen == [("NIFTY", 22048.0, 1_800_000_000_000_000_000)]


def test_cache_seed_rejects_mismatched_identity_and_does_not_overwrite_newer_tick(monkeypatch):
    from app.market_data import live_synthetic_underlying as underlying
    now = 1_800_000_000_100_000_000
    monkeypatch.setattr(underlying, "time_ns", lambda: now)
    descriptor = _descriptor()
    seen = []
    feed, tracker = _underlying_feed(lambda *args: seen.append(args))
    feed._feed = _CacheReader({
        descriptor.key.value: {
            "exchange": "NSE",
            "segment": "NSE",
            "token": "26000",
            "symbol": "NIFTY",
            "timestamp_ns": 1_800_000_000_000_000_000,
            "ltp": 22048.0,
        }
    })

    assert feed._apply_price("NIFTY", 22120.0, 1_800_000_000_200_000_000)
    assert feed._seed_from_cache((descriptor,)) == 0
    assert tracker.atm("NIFTY") == 22100.0
    assert seen == [("NIFTY", 22120.0, 1_800_000_000_200_000_000)]


def test_cache_seed_rejects_wrong_instrument_and_invalid_quote():
    descriptor = _descriptor()
    feed, tracker = _underlying_feed()
    feed._feed = _CacheReader({
        descriptor.key.value: {
            "exchange": "NFO",
            "segment": "NFO",
            "token": "26000",
            "symbol": "NIFTY",
            "timestamp_ns": 1_800_000_000_000_000_000,
            "ltp": 22048.0,
        }
    })

    assert feed._seed_from_cache((descriptor,)) == 0
    assert tracker.atm("NIFTY") is None

    feed._feed.snapshots[descriptor.key.value] = {
        "exchange": "NSE",
        "segment": "NSE",
        "token": "26000",
        "symbol": "NIFTY",
        "timestamp_ns": 1_800_000_000_000_000_000,
        "ltp": float("nan"),
    }
    assert feed._seed_from_cache((descriptor,)) == 0
    assert tracker.atm("NIFTY") is None
