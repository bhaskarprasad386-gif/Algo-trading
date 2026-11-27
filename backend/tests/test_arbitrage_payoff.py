from app.backtesting.arbitrage_payoff import build_box_payoff, build_strategy_payoff
from app.execution.payoff import payoff_at_price


def option(ts, strike, cb, ca, pb, pa, expiry=20261231):
    return {"timestamp_ns": ts, "underlying": "ABC", "expiry": expiry, "strike": strike,
            "call_bid": cb, "call_ask": ca, "put_bid": pb, "put_ask": pa, "lot_size": 1}


def test_box_payoff_contains_four_real_entry_legs_and_fixed_expiry_payoff():
    event = {"low": option(1, 100, 6, 4, 5, 3), "high": option(1, 110, 2, 3, 2, 2)}
    result = build_box_payoff(event)
    assert len(result.legs) == 4
    assert [(leg.kind, leg.side, leg.strike, leg.entry_price) for leg in result.legs] == [
        ("CALL", "BUY", 100.0, 4.0),
        ("CALL", "SELL", 110.0, 2.0),
        ("PUT", "BUY", 110.0, 2.0),
        ("PUT", "SELL", 100.0, 5.0),
    ]
    assert payoff_at_price(result.legs, 90.0) == 11.0
    assert payoff_at_price(result.legs, 105.0) == 11.0
    assert payoff_at_price(result.legs, 120.0) == 11.0
    assert result.metadata["quote_timestamp_ns"] == 1
    assert build_strategy_payoff("box-spread", event).strategy_id == "box-spread"
