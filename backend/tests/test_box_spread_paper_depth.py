from types import SimpleNamespace

import pytest

from app.scanner.box_spread_paper_depth import (
    allocate_depth,
    depth_levels,
    max_complete_lots,
    select_complete_lots,
)


ROLES = ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")


def _record(bids, asks):
    return SimpleNamespace(
        bid=99.0,
        ask=101.0,
        bid_qty=10,
        ask_qty=10,
        payload={
            "best_5_buy_data": [
                {"price": str(int(price * 100)), "quantity": str(qty)}
                for price, qty in bids
            ],
            "best_5_sell_data": [
                {"price": str(int(price * 100)), "quantity": str(qty)}
                for price, qty in asks
            ],
        },
    )


def test_depth_levels_normalize_rupees_and_sort_price_priority():
    record = _record([(99, 10), (100, 5)], [(102, 4), (101, 6)])
    assert depth_levels(record, "BID") == [(100.0, 5.0), (99.0, 10.0)]
    assert depth_levels(record, "ASK") == [(101.0, 6.0), (102.0, 4.0)]


def test_depth_levels_fall_back_to_canonical_l1_only():
    record = SimpleNamespace(bid=99.5, ask=100.5, bid_qty=20, ask_qty=15, payload={})
    assert depth_levels(record, "BID") == [(99.5, 20.0)]
    assert depth_levels(record, "ASK") == [(100.5, 15.0)]


def test_allocate_depth_calculates_weighted_average_across_levels():
    result = allocate_depth([(100.0, 10), (99.0, 20)], 15)
    assert result == {"quantity": 15.0, "vwap": pytest.approx((1000 + 495) / 15), "complete": True}


def test_entry_quantity_falls_back_until_all_four_legs_are_complete():
    depth = {
        "LOW_CE": [(100.0, 150)],
        "HIGH_CE": [(90.0, 100)],
        "HIGH_PE": [(80.0, 150)],
        "LOW_PE": [(70.0, 125)],
    }
    selected = select_complete_lots(3, 50, depth, roles=ROLES)
    assert selected is not None
    assert selected["lots"] == 2
    assert selected["quantity"] == 100
    assert all(item["complete"] for item in selected["allocations"].values())


def test_entry_is_rejected_if_even_one_complete_lot_is_unavailable():
    depth = {role: [(100.0, 50)] for role in ROLES}
    depth["HIGH_PE"] = [(80.0, 49)]
    assert select_complete_lots(3, 50, depth, roles=ROLES) is None


def test_exit_capacity_is_limited_by_weakest_leg():
    depth = {
        "LOW_CE": [(100.0, 175)],
        "HIGH_CE": [(90.0, 150)],
        "HIGH_PE": [(80.0, 99)],
        "LOW_PE": [(70.0, 200)],
    }
    assert max_complete_lots(50, depth, roles=ROLES) == 1


def test_sell_limit_filters_bid_levels_below_limit():
    from app.scanner.box_spread_paper_depth import limit_eligible_depth
    assert limit_eligible_depth([(10.5, 5), (10.0, 10), (9.5, 20)], "SELL", 10.0) == [
        (10.5, 5), (10.0, 10),
    ]


def test_buy_limit_filters_ask_levels_above_limit():
    from app.scanner.box_spread_paper_depth import limit_eligible_depth
    assert limit_eligible_depth([(9.5, 5), (10.0, 10), (10.5, 20)], "BUY", 10.0) == [
        (9.5, 5), (10.0, 10),
    ]


def test_limit_depth_rejects_non_positive_or_invalid_limits():
    from app.scanner.box_spread_paper_depth import limit_eligible_depth
    import pytest
    with pytest.raises(ValueError):
        limit_eligible_depth([(10.0, 5)], "SELL", 0)
    with pytest.raises(ValueError):
        limit_eligible_depth([(10.0, 5)], "HOLD", 10)
