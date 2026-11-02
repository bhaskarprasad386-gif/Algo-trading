from types import SimpleNamespace

from app.scanner.box_spread_depth_shadow_compare import compare_box_depth


def candidate(direction="LONG"):
    low = SimpleNamespace(strike=100, lot_size=10)
    high = SimpleNamespace(strike=110, lot_size=10)
    return SimpleNamespace(low=low, high=high, direction=direction, executable_edge=5.0)


def legs(*, bids=((10, 100),), asks=((11, 100),), stamp=10_000_000_000):
    return {
        role: {
            "freshness": "FRESH", "depth_status": "DEPTH_FULL",
            "timestamp_ns": stamp, "provider_age_seconds": 0.1,
            "arrival_age_seconds": 0.1, "bids": list(bids), "asks": list(asks),
        }
        for role in ("low_call", "high_call", "high_put", "low_put")
    }


def test_long_box_uses_correct_four_leg_depth_sides_and_same_quantity():
    data = legs(bids=((10, 100),), asks=((11, 100),))
    result = compare_box_depth(candidate("LONG"), data, target_lots=2, now_ns=10_100_000_000, now_monotonic=1.0)
    assert result["status"] == "COMPLETE"
    assert result["quantity"] == 20
    assert result["allocations"]["low_call"]["side"] == "ASK"
    assert result["allocations"]["high_call"]["side"] == "BID"
    assert result["allocations"]["high_put"]["side"] == "ASK"
    assert result["allocations"]["low_put"]["side"] == "BID"
    assert result["depth_edge_per_unit"] == 8


def test_short_box_reverses_all_four_sides():
    result = compare_box_depth(candidate("SHORT"), legs(), now_ns=10_100_000_000, now_monotonic=1.0)
    assert result["status"] == "COMPLETE"
    assert {role: item["side"] for role, item in result["allocations"].items()} == {
        "low_call": "BID", "high_call": "ASK", "high_put": "BID", "low_put": "ASK"
    }


def test_one_leg_insufficient_depth_rejects_entire_basket():
    data = legs()
    data["low_put"]["bids"] = [(10, 9)]
    result = compare_box_depth(candidate(), data, now_ns=10_100_000_000, now_monotonic=1.0)
    assert result["status"] == "INCOMPLETE"
    assert result["reason"] == "INSUFFICIENT_DEPTH"


def test_l1_only_and_stale_legs_are_not_used_as_depth():
    data = legs()
    data["high_put"]["depth_status"] = "L1_ONLY"
    result = compare_box_depth(candidate(), data, now_ns=10_100_000_000, now_monotonic=1.0)
    assert result["reason"] == "DEPTH_NOT_EXECUTABLE"
    data = legs()
    data["low_call"]["freshness"] = "STALE"
    result = compare_box_depth(candidate(), data, now_ns=10_100_000_000, now_monotonic=1.0)
    assert result["reason"] == "DEPTH_NOT_EXECUTABLE"


def test_cross_leg_timestamp_skew_rejects_comparison():
    data = legs()
    data["low_call"]["timestamp_ns"] += 3_000_000_000
    result = compare_box_depth(candidate(), data, now_ns=10_100_000_000, now_monotonic=1.0)
    assert result["reason"] == "CROSS_LEG_SKEW"


def test_invalid_or_nonfinite_levels_do_not_become_executable():
    data = legs()
    data["low_call"]["asks"] = [(float("nan"), 100), (11, 0)]
    result = compare_box_depth(candidate(), data, now_ns=10_100_000_000, now_monotonic=1.0)
    assert result["reason"] == "INSUFFICIENT_DEPTH"


def test_missing_leg_and_invalid_target_quantity_fail_closed():
    data = legs()
    data.pop("low_put")
    assert compare_box_depth(candidate(), data)["reason"] == "MISSING_LEG"
    try:
        compare_box_depth(candidate(), legs(), target_lots=0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero lots must be rejected")
