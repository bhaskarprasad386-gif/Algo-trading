from app.backtesting.strategy_payoff import (
    box_payoff_legs,
    calendar_payoff_legs,
    cash_future_payoff_legs,
    synthetic_cash_carry_payoff_legs,
)


def test_box_builds_four_real_entry_legs():
    legs = box_payoff_legs({
        "low": {"strike": 100, "call_ask": 5, "call_bid": 4, "put_ask": 6, "put_bid": 5, "lot_size": 50},
        "high": {"strike": 110, "call_ask": 2, "call_bid": 1, "put_ask": 12, "put_bid": 11, "lot_size": 50},
    })
    assert [(x.kind, x.side, x.strike, x.entry_price, x.multiplier) for x in legs] == [
        ("CALL", "BUY", 100, 5.0, 50.0),
        ("CALL", "SELL", 110, 1.0, 50.0),
        ("PUT", "BUY", 100, 6.0, 50.0),
        ("PUT", "SELL", 110, 11.0, 50.0),
    ]


def test_synthetic_builds_option_pair_and_opposite_future():
    legs = synthetic_cash_carry_payoff_legs({
        "option": {"strike": 100, "call_ask": 7, "call_bid": 6, "put_ask": 8, "put_bid": 7},
        "future": {"bid": 105, "ask": 106, "lot_size": 25},
    })
    assert [(x.kind, x.side, x.entry_price) for x in legs] == [
        ("CALL", "BUY", 7.0), ("PUT", "SELL", 7.0), ("FUTURE", "SELL", 105.0)
    ]


def test_cash_future_builds_spot_and_future_legs():
    legs = cash_future_payoff_legs({
        "cash_future": {"spot_bid": 100, "spot_ask": 101, "future_bid": 104, "future_ask": 105, "lot_size": 10}
    })
    assert [(x.kind, x.side, x.entry_price, x.quantity) for x in legs] == [
        ("SPOT", "BUY", 101.0, 10.0), ("FUTURE", "SELL", 104.0, 10.0)
    ]


def test_calendar_preserves_near_far_contract_prices_and_expiry_metadata_is_not_collapsed():
    legs = calendar_payoff_legs({
        "near": {"expiry": 20260924, "bid": 4, "ask": 5, "lot_size": 75, "strike": 25000, "option_type": "CALL"},
        "far": {"expiry": 20261029, "bid": 9, "ask": 10, "lot_size": 75, "strike": 25000, "option_type": "CALL"},
    })
    assert [(x.kind, x.side, x.entry_price, x.quantity, x.strike) for x in legs] == [
        ("CALL", "BUY", 5.0, 75.0, 25000), ("CALL", "SELL", 9.0, 75.0, 25000)
    ]
