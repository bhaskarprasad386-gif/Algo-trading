from app.execution.payoff import PayoffLeg, break_even_points


def test_break_even_points_returns_a_list_contract():
    leg = PayoffLeg("CALL", "BUY", 100.0, 5.0, 1)
    points = break_even_points((leg,), tuple(float(x) for x in range(90, 111)))
    assert isinstance(points, list)
    assert points == [105.0]
