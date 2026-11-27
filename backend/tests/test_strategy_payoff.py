from app.backtesting.strategy_payoff import box_payoff_legs, build_strategy_payoff_legs


def test_box_builds_four_real_entry_legs():
    event = {
        "low": {"strike": 100, "call_ask": 5, "call_bid": 4, "put_ask": 6, "put_bid": 5, "lot_size": 50},
        "high": {"strike": 110, "call_ask": 2, "call_bid": 1, "put_ask": 12, "put_bid": 11, "lot_size": 50},
    }
    legs = box_payoff_legs(event)
    assert [(x.kind, x.side, x.strike, x.entry_price, x.multiplier) for x in legs] == [
        ("CALL", "BUY", 100, 5.0, 50.0),
        ("CALL", "SELL", 110, 1.0, 50.0),
        ("PUT", "BUY", 100, 6.0, 50.0),
        ("PUT", "SELL", 110, 11.0, 50.0),
    ]
    assert len(build_strategy_payoff_legs("box-spread", event)) == 4
