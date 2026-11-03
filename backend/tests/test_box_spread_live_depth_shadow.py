from types import SimpleNamespace

from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor
from app.scanner.box_spread_live_depth_shadow import BoxLiveDepthShadowFeed


class FakeFeed:
    def __init__(self):
        self.started = []
        self.stopped = 0

    def start(self, descriptors, callback):
        self.started.append((tuple(descriptors), callback))

    def stop(self):
        self.stopped += 1


def _descriptor(token="123"):
    return InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", token),
        symbol="NIFTY",
        instrument_type="option",
        exchange="NFO",
        segment="NFO",
        expiry="06NOV2026",
        strike=25000,
        option_type="CE",
        lot_size=65,
        underlying="NIFTY",
    )


def _record(*, timestamp_ns, token="123", payload=None, bid=100.0, ask=101.0):
    return SimpleNamespace(
        instrument=InstrumentKey("NFO", "NFO", token),
        symbol="NIFTY06NOV2625000CE",
        timestamp_ns=timestamp_ns,
        bid=bid,
        ask=ask,
        bid_qty=10,
        ask_qty=10,
        payload=payload or {},
    )


def _five_levels(start):
    return [{"price": str((start + i) * 100), "quantity": "25"} for i in range(5)]


def test_disabled_shadow_does_not_register_a_feed_consumer():
    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(feed=feed)
    assert shadow.start([_descriptor()]) is False
    assert feed.started == []
    assert shadow.snapshot()["enabled"] is False


def test_shadow_subscribes_only_option_descriptors_and_reads_five_levels():
    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    descriptor = _descriptor()
    assert shadow.start([descriptor]) is True
    assert len(feed.started) == 1
    callback = feed.started[0][1]

    now = __import__("time").time_ns()
    callback(_record(
        timestamp_ns=now,
        payload={
            "best_5_buy_data": _five_levels(95),
            "best_5_sell_data": _five_levels(101),
        },
    ))
    snapshot = shadow.snapshot(now_ns=now)
    assert snapshot["record_count"] == 1
    row = snapshot["records"][0]
    assert row["depth_status"] == "DEPTH_FULL"
    assert row["freshness"] == "FRESH"
    assert len(row["bids"]) == len(row["asks"]) == 5
    assert row["bids"][0] == (99.0, 25.0)
    assert row["asks"][0] == (101.0, 25.0)


def test_l1_only_is_never_mislabelled_as_full_depth_or_falls_back_to_l1():
    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    shadow.start([_descriptor()])
    now = __import__("time").time_ns()
    feed.started[0][1](_record(timestamp_ns=now, payload={}))
    row = shadow.snapshot(now_ns=now)["records"][0]
    assert row["depth_status"] == "L1_ONLY"
    assert row["bids"] == []
    assert row["asks"] == []


def test_shadow_rejects_crossed_depth_and_marks_old_provider_ticks_stale():
    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    shadow.start([_descriptor()])
    now = __import__("time").time_ns()
    feed.started[0][1](_record(
        timestamp_ns=now - 10_000_000_000,
        payload={
            "best_5_buy_data": _five_levels(102),
            "best_5_sell_data": _five_levels(101),
        },
    ))
    row = shadow.snapshot(now_ns=now)["records"][0]
    assert row["depth_status"] == "DEPTH_INVALID"
    assert row["freshness"] == "STALE"


def test_shadow_cache_is_bounded_and_stop_detaches_only_its_consumer():
    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, max_records=1, feed=feed)
    shadow.start([_descriptor("123")])
    callback = feed.started[0][1]
    now = __import__("time").time_ns()
    callback(_record(timestamp_ns=now, token="123", payload={}))
    callback(_record(timestamp_ns=now, token="456", payload={}))
    assert shadow.snapshot(now_ns=now)["record_count"] == 1
    shadow.stop()
    assert feed.stopped == 1
    assert shadow.snapshot()["record_count"] == 0


def test_shadow_rejects_non_option_subscription():
    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    future = InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", "999"),
        symbol="NIFTY",
        instrument_type="future",
        exchange="NFO",
        segment="NFO",
    )
    import pytest
    with pytest.raises(ValueError, match="option descriptors only"):
        shadow.start([future])
    assert feed.started == []


def test_shadow_start_failure_clears_only_its_consumer_and_cache():
    import pytest

    class FailingFeed(FakeFeed):
        def start(self, descriptors, callback):
            self.started.append((tuple(descriptors), callback))
            raise RuntimeError("subscribe failed")

    feed = FailingFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    with pytest.raises(RuntimeError, match="subscribe failed"):
        shadow.start([_descriptor()])
    assert feed.stopped == 1
    assert shadow.snapshot()["record_count"] == 0
    assert shadow.snapshot()["running"] is False



def test_four_leg_edge_uses_available_partial_depth_and_common_quantity():
    """Four levels on one leg are valid when all four legs have enough liquidity."""
    from dataclasses import replace
    import time

    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    specs = [
        ("101", 25000, "CE"),
        ("102", 26000, "CE"),
        ("103", 26000, "PE"),
        ("104", 25000, "PE"),
    ]
    descriptors = [
        replace(
            _descriptor(token),
            key=InstrumentKey("NFO", "NFO", token),
            strike=strike,
            option_type=option_type,
        )
        for token, strike, option_type in specs
    ]
    shadow.start(descriptors)
    callback = feed.started[0][1]
    now = time.time_ns()

    for index, (token, strike, option_type) in enumerate(specs):
        count = 4 if index == 0 else 5
        bids = [{"price": str((100 - level) * 100), "quantity": "50"} for level in range(count)]
        asks = [{"price": str((110 + level) * 100), "quantity": "50"} for level in range(count)]
        callback(_record(
            timestamp_ns=now,
            token=token,
            payload={"best_5_buy_data": bids, "best_5_sell_data": asks},
        ))

    low = SimpleNamespace(
        underlying="NIFTY", expiry=20261106, strike=25000, lot_size=65,
        call_timestamp_ns=now, put_timestamp_ns=now, timestamp_ns=now,
    )
    high = SimpleNamespace(
        underlying="NIFTY", expiry=20261106, strike=26000, lot_size=65,
        call_timestamp_ns=now, put_timestamp_ns=now, timestamp_ns=now,
    )
    opportunity = SimpleNamespace(low=low, high=high, direction="LONG", executable_edge=5.0)

    result = shadow.compare_opportunity(opportunity, now_ns=now)
    assert result["status"] == "COMPLETE"
    assert result["max_executable_lots"] == 3
    assert result["quantity"] == 195
    assert min(result["depth_capacity_by_leg"].values()) == 200
    assert len(result["legs"]) == 4
    assert all(leg["selected_quantity"] == 195 for leg in result["legs"])
    assert result["depth_edge_per_unit"] > 0
