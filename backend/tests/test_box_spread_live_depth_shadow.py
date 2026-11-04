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



def test_four_leg_edge_accepts_one_level_on_weakest_leg_for_two_lots():
    """One level on one leg is valid when its quantity supports two common lots."""
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
        count = 1 if index == 0 else (4 if index == 1 else 5)
        level_quantity = "130" if index == 0 else "50"
        bids = [{"price": str((100 - level) * 100), "quantity": level_quantity} for level in range(count)]
        asks = [{"price": str((110 + level) * 100), "quantity": level_quantity} for level in range(count)]
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
    assert result["max_executable_lots"] == 2
    assert result["quantity"] == 130
    assert min(result["depth_capacity_by_leg"].values()) == 130
    assert len(result["legs"]) == 4
    assert all(leg["selected_quantity"] == 130 for leg in result["legs"])
    assert all(len(leg["depth_levels"]) in {1, 4, 5} for leg in result["legs"])
    weakest = next(leg for leg in result["legs"] if leg["role"] == "LOW_CE")
    assert weakest["depth_levels"] == [
        {"price": 110.0, "quantity": 130.0},
    ]
    assert weakest["bid_depth_levels"] == [
        {"price": 100.0, "quantity": 130.0},
    ]
    assert weakest["ask_depth_levels"] == [
        {"price": 110.0, "quantity": 130.0},
    ]
    assert result["depth_edge_per_unit"] > 0


def test_snapshot_reports_per_underlying_expiry_fresh_depth_readiness():
    from dataclasses import replace
    import time

    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    specs = [
        ("201", 25000, "CE"),
        ("202", 26000, "CE"),
        ("203", 26000, "PE"),
        ("204", 25000, "PE"),
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
    for token, _, _ in specs:
        callback(_record(
            timestamp_ns=now,
            token=token,
            payload={
                "best_5_buy_data": [{"price": str((95 + i) * 100), "quantity": "30"} for i in range(5)],
                "best_5_sell_data": [{"price": str((101 + i) * 100), "quantity": "30"} for i in range(5)],
            },
        ))

    snapshot = shadow.snapshot(now_ns=now)
    assert snapshot["consumer"] == "box-live-depth-shadow"
    assert snapshot["readiness"] == [{
        "underlying": "NIFTY",
        "expiry": 20261106,
        "subscribed_contracts": 4,
        "fresh_contracts": 4,
        "fresh_ce": 2,
        "fresh_pe": 2,
        "executable_depth_contracts": 4,
        "executable_depth_ce": 2,
        "executable_depth_pe": 2,
        "full_depth_ce": 2,
        "full_depth_pe": 2,
        "state": "READY",
    }]


def test_snapshot_keeps_depth_readiness_waiting_until_both_option_sides_are_fresh():
    from dataclasses import replace
    import time

    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    specs = [
        ("301", 25000, "CE"),
        ("302", 26000, "CE"),
        ("303", 26000, "PE"),
        ("304", 25000, "PE"),
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
    for token, _, option_type in specs[:2]:
        callback(_record(
            timestamp_ns=now,
            token=token,
            payload={
                "best_5_buy_data": _five_levels(95),
                "best_5_sell_data": _five_levels(101),
            },
        ))

    readiness = shadow.snapshot(now_ns=now)["readiness"][0]
    assert readiness["fresh_ce"] == 2
    assert readiness["fresh_pe"] == 0
    assert readiness["state"] == "WAITING_FOR_DEPTH"


def test_depth_shadow_supports_distinct_consumer_namespace_without_touching_default():
    active_feed = FakeFeed()
    staged_feed = FakeFeed()
    active = BoxLiveDepthShadowFeed(enabled=True, feed=active_feed)
    staged = BoxLiveDepthShadowFeed(
        enabled=True,
        feed=staged_feed,
        consumer="box-staged-42-live-depth-shadow",
    )
    active.start([_descriptor("701")])
    staged.start([_descriptor("702")])

    assert active.snapshot()["consumer"] == "box-live-depth-shadow"
    assert staged.snapshot()["consumer"] == "box-staged-42-live-depth-shadow"

    staged.stop()
    assert staged_feed.stopped == 1
    assert active_feed.stopped == 0
    assert active.snapshot()["running"] is True


def test_partial_depth_below_two_lot_capacity_does_not_mark_staged_feed_ready():
    from dataclasses import replace
    import time

    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    specs = [("401", 25000, "CE"), ("402", 26000, "CE"), ("403", 26000, "PE"), ("404", 25000, "PE")]
    descriptors = [
        replace(_descriptor(token), key=InstrumentKey("NFO", "NFO", token), strike=strike, option_type=option_type)
        for token, strike, option_type in specs
    ]
    shadow.start(descriptors)
    callback = feed.started[0][1]
    now = time.time_ns()
    for index, (token, _, _) in enumerate(specs):
        levels = 4 if index == 0 else 5
        callback(_record(timestamp_ns=now, token=token, payload={
            "best_5_buy_data": _five_levels(95)[:levels],
            "best_5_sell_data": _five_levels(101)[:levels],
        }))
    readiness = shadow.snapshot(now_ns=now)["readiness"][0]
    assert readiness["fresh_ce"] == 2
    assert readiness["fresh_pe"] == 2
    assert readiness["executable_depth_contracts"] == 0
    assert readiness["executable_depth_ce"] == 0
    assert readiness["executable_depth_pe"] == 0
    assert readiness["full_depth_ce"] == 1
    assert readiness["full_depth_pe"] == 2
    assert readiness["state"] == "WAITING_FOR_DEPTH"



def test_one_level_depth_is_ready_when_every_option_has_two_lots_on_both_sides():
    from dataclasses import replace
    import time

    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    specs = [("501", 25000, "CE"), ("502", 26000, "CE"), ("503", 26000, "PE"), ("504", 25000, "PE")]
    descriptors = [
        replace(_descriptor(token), key=InstrumentKey("NFO", "NFO", token), strike=strike, option_type=option_type)
        for token, strike, option_type in specs
    ]
    shadow.start(descriptors)
    callback = feed.started[0][1]
    now = time.time_ns()
    for index, (token, _, _) in enumerate(specs):
        count = 1 if index == 0 else (4 if index == 1 else 5)
        quantity = "130" if index == 0 else "50"
        bids = [{"price": str((100 - level) * 100), "quantity": quantity} for level in range(count)]
        asks = [{"price": str((110 + level) * 100), "quantity": quantity} for level in range(count)]
        callback(_record(timestamp_ns=now, token=token, payload={
            "best_5_buy_data": bids,
            "best_5_sell_data": asks,
        }))
    readiness = shadow.snapshot(now_ns=now)["readiness"][0]
    assert readiness["fresh_ce"] == 2
    assert readiness["fresh_pe"] == 2
    assert readiness["executable_depth_contracts"] == 4
    assert readiness["executable_depth_ce"] == 2
    assert readiness["executable_depth_pe"] == 2
    assert readiness["full_depth_ce"] == 0
    assert readiness["full_depth_pe"] == 2
    assert readiness["state"] == "READY"
