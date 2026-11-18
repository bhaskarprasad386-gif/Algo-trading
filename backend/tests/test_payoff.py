import pytest

from app.execution.payoff import PayoffLeg, break_even_points, payoff_at_price, payoff_summary


def test_long_future_payoff():
    leg = PayoffLeg("FUTURE", "BUY", None, 100.0, 10)
    assert payoff_at_price((leg,), 110.0) == 100.0
    assert payoff_at_price((leg,), 90.0) == -100.0


def test_long_call_break_even():
    leg = PayoffLeg("CALL", "BUY", 100.0, 5.0, 10)
    prices = tuple(float(x) for x in range(90, 121))
    points = break_even_points((leg,), prices)
    assert points == [105.0]


def test_short_put_profit_is_bounded_by_grid():
    leg = PayoffLeg("PUT", "SELL", 100.0, 4.0, 10)
    summary = payoff_summary((leg,), (80.0, 90.0, 100.0, 110.0, 120.0))
    assert summary["max_profit"] == 40.0
    assert summary["max_loss"] == -160.0


def test_option_requires_strike():
    with pytest.raises(ValueError):
        PayoffLeg("CALL", "BUY", None, 2.0, 1)
