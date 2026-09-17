from app.backtesting.arbitrage_payoff import (
    build_box_payoff,
    build_calendar_payoff,
    build_cash_future_payoff,
    build_synthetic_cash_carry_payoff,
)
from app.execution.payoff import payoff_at_price
import pytest


def option(ts, strike, cb, ca, pb, pa, expiry=20261231, lot_size=1, instrument_class=None):
    result = {"timestamp_ns": ts, "underlying": "ABC", "expiry": expiry, "strike": strike,
              "call_bid": cb, "call_ask": ca, "put_bid": pb, "put_ask": pa, "lot_size": lot_size}
    if instrument_class is not None:
        result["instrument_class"] = instrument_class
    return result


def test_box_payoff_contains_four_real_entry_legs_and_fixed_expiry_payoff():
    result = build_box_payoff({
        "low": option(1, 100, 6, 4, 5, 3),
        "high": option(1, 110, 2, 3, 2, 2),
    })
    assert len(result.legs) == 4
    assert [(leg.kind, leg.side, leg.strike, leg.entry_price) for leg in result.legs] == [
        ("CALL", "BUY", 100.0, 4.0),
        ("CALL", "SELL", 110.0, 2.0),
        ("PUT", "BUY", 110.0, 2.0),
        ("PUT", "SELL", 100.0, 5.0),
    ]
    # The four real fills produce a net premium credit of 1, so expiry payoff
    # is the 10-point box value plus that 1-point credit.
    assert payoff_at_price(result.legs, 90.0) == 11.0
    assert payoff_at_price(result.legs, 105.0) == 11.0
    assert payoff_at_price(result.legs, 120.0) == 11.0
    assert result.metadata["quote_timestamp_ns"] == 1


def test_box_payoff_rejects_mismatched_timestamps():
    with pytest.raises(ValueError, match="share timestamp"):
        build_box_payoff({
            "low": option(1, 100, 6, 4, 5, 3),
            "high": option(2, 110, 2, 3, 2, 2),
        })


def test_box_payoff_rejects_mismatched_lot_sizes():
    with pytest.raises(ValueError, match="share lot size"):
        build_box_payoff({
            "low": option(1, 100, 6, 4, 5, 3, lot_size=10),
            "high": option(1, 110, 2, 3, 2, 2, lot_size=20),
        })


def test_box_payoff_rejects_mismatched_instrument_classes():
    with pytest.raises(ValueError, match="share instrument class"):
        build_box_payoff({
            "low": option(1, 100, 6, 4, 5, 3, instrument_class="STOCK"),
            "high": option(1, 110, 2, 3, 2, 2, instrument_class="INDEX"),
        })


def test_synthetic_payoff_contains_option_pair_and_opposite_future():
    result = build_synthetic_cash_carry_payoff({
        "option": option(1, 100, 10, 11, 1, 2),
        "future": {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20261231,
                   "bid": 111, "ask": 112, "lot_size": 1},
    })
    assert [(leg.kind, leg.side) for leg in result.legs] == [
        ("CALL", "BUY"), ("PUT", "SELL"), ("FUTURE", "SELL")
    ]
    assert result.metadata["strike"] == 100.0


def test_cash_future_payoff_preserves_executable_entry_sides():
    result = build_cash_future_payoff({
        "cash_future": {"timestamp_ns": 1, "underlying": "ABC", "spot_bid": 100,
            "spot_ask": 101, "future_bid": 104, "future_ask": 105, "expiry": 20261231,
            "lot_size": 10, "carry_factor": 1.0}
    })
    assert [(leg.kind, leg.side, leg.entry_price) for leg in result.legs] == [
        ("SPOT", "BUY", 101.0), ("FUTURE", "SELL", 104.0)
    ]


def test_calendar_payoff_retains_both_expiries_in_metadata():
    result = build_calendar_payoff({
        "near": {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924,
                  "bid": 10, "ask": 11, "lot_size": 1, "strike": 100, "option_type": "CALL"},
        "far": {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20261029,
                 "bid": 15, "ask": 16, "lot_size": 1, "strike": 100, "option_type": "CALL"},
    })
    assert len(result.legs) == 2
    assert result.metadata["near_expiry"] == 20260924
    assert result.metadata["far_expiry"] == 20261029
