from app.backtesting.arbitrage_backtester import BoxSpreadBacktester, LiquidityPolicy, OptionQuote


def option(ts=1, strike=100, cb=7, ca=8, pb=6, pa=7):
    return OptionQuote(ts, "ABC", 20261231, strike, cb, ca, pb, pa, lot_size=10)


def test_long_box_uses_executable_bid_ask_and_expiry_width():
    low = option(strike=100, cb=12, ca=13, pb=8, pa=9)
    high = option(strike=110, cb=4, ca=5, pb=1, pa=9)
    opp = BoxSpreadBacktester.evaluate(low, high, direction="LONG")
    assert opp is None


def test_long_box_profit_when_executable_debit_is_below_width():
    low = option(strike=100, cb=12, ca=13, pb=8, pa=8.2)
    high = option(strike=110, cb=7.1, ca=7.2, pb=4.9, pa=11.3)
    opp = BoxSpreadBacktester.evaluate(low, high)
    assert opp is not None
    assert round(opp.executable_edge, 6) == 0.8
    assert round(opp.edge_per_lot, 6) == 8.0


def test_short_box_uses_high_put_bid_and_low_put_ask():
    low = option(strike=100, cb=11, ca=11, pb=1, pa=2)
    high = option(strike=110, cb=2, ca=2, pb=8, pa=9)
    opp = BoxSpreadBacktester.evaluate(low, high, direction="SHORT")
    assert opp is not None
    assert opp.executable_edge == 5
    assert opp.edge_per_lot == 50


def test_long_box_uses_high_put_ask_and_low_put_bid():
    low = option(strike=100, cb=10, ca=10, pb=1, pa=2)
    high = option(strike=110, cb=8, ca=9, pb=2, pa=3)
    opp = BoxSpreadBacktester.evaluate(low, high, direction="LONG")
    assert opp is not None
    assert opp.executable_edge == 6
    assert opp.edge_per_lot == 60


def test_box_rejects_mismatched_expiry():
    low = option()
    high = OptionQuote(1, "ABC", 20270131, 110, 4, 5, 1, 2, 10)
    try:
        BoxSpreadBacktester.evaluate(low, high)
    except ValueError as exc:
        assert "expiry" in str(exc)
    else:
        raise AssertionError("expected expiry mismatch rejection")


def test_box_rejects_invalid_option_quotes():
    low = option(cb=float("nan"))
    high = option(strike=110)
    try:
        BoxSpreadBacktester.evaluate(low, high)
    except ValueError as exc:
        assert "finite" in str(exc)
    else:
        raise AssertionError("expected non-finite quote rejection")


def test_box_rejects_mismatched_instrument_class():
    low = option()
    high = OptionQuote(1, "ABC", 20261231, 110, 4, 5, 1, 2, 10, instrument_class="INDEX")
    try:
        BoxSpreadBacktester.evaluate(low, high)
    except ValueError as exc:
        assert "instrument class" in str(exc)
    else:
        raise AssertionError("expected instrument class mismatch rejection")



def test_liquidity_policy_filters_illiquid_quotes():
    policy = LiquidityPolicy(min_option_volume=1000, min_option_oi=5000, max_spread_pct=3)
    assert policy.accepts(volume=1000, oi=5000, bid=100, ask=103)
    assert not policy.accepts(volume=999, oi=5000, bid=100, ask=101)
    assert not policy.accepts(volume=1000, oi=5000, bid=100, ask=104)
