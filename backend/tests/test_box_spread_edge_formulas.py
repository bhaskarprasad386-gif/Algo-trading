from app.backtesting.arbitrage_backtester import BoxSpreadBacktester, OptionQuote


def quote(strike, *, call_bid, call_ask, put_bid, put_ask):
    return OptionQuote(
        timestamp_ns=1_800_000_000_000_000_000,
        underlying="NIFTY", expiry=20261029, strike=strike,
        call_bid=call_bid, call_ask=call_ask, put_bid=put_bid, put_ask=put_ask,
        lot_size=25, instrument_class="INDEX", volume=100, oi=100,
    )


def test_long_box_edge_uses_exact_executable_bid_ask_formula():
    low = quote(100, call_bid=10, call_ask=12, put_bid=8, put_ask=9)
    high = quote(120, call_bid=3, call_ask=4, put_bid=18, put_ask=18)
    result = BoxSpreadBacktester.evaluate(low, high, direction="LONG")
    expected = (120 - 100) - ((12 + 18) - (8 + 3))
    assert result is not None
    assert result.executable_edge == expected == 1


def test_short_box_edge_uses_exact_executable_bid_ask_formula():
    low = quote(100, call_bid=15, call_ask=16, put_bid=10, put_ask=11)
    high = quote(120, call_bid=2, call_ask=3, put_bid=25, put_ask=26)
    result = BoxSpreadBacktester.evaluate(low, high, direction="SHORT")
    expected = ((15 + 25) - (10 + 2)) - (120 - 100)
    assert result is not None
    assert result.executable_edge == expected == 6


def test_long_and_short_box_edges_return_none_when_non_positive():
    low = quote(100, call_bid=5, call_ask=15, put_bid=4, put_ask=14)
    high = quote(120, call_bid=1, call_ask=11, put_bid=10, put_ask=30)
    assert BoxSpreadBacktester.evaluate(low, high, direction="LONG") is None
    assert BoxSpreadBacktester.evaluate(low, high, direction="SHORT") is None
