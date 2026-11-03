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
    expected = ((15 + 25) - (11 + 3)) - (120 - 100)
    assert result is not None
    assert result.executable_edge == expected == 6


def test_long_and_short_box_edges_return_none_when_non_positive():
    low = quote(100, call_bid=5, call_ask=15, put_bid=4, put_ask=14)
    high = quote(120, call_bid=1, call_ask=11, put_bid=10, put_ask=30)
    assert BoxSpreadBacktester.evaluate(low, high, direction="LONG") is None
    assert BoxSpreadBacktester.evaluate(low, high, direction="SHORT") is None



def test_live_box_spread_max_lots_uses_minimum_of_directional_four_leg_depth():
    from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot

    low = OptionQuote(
        timestamp_ns=1_800_000_000_000_000_000,
        underlying="NIFTY", expiry=20261029, strike=100,
        call_bid=10, call_ask=12, put_bid=8, put_ask=9,
        lot_size=10, instrument_class="INDEX", volume=100, oi=100,
        call_bid_qty=50, call_ask_qty=25, put_bid_qty=30, put_ask_qty=60,
    )
    high = OptionQuote(
        timestamp_ns=1_800_000_000_000_000_000,
        underlying="NIFTY", expiry=20261029, strike=120,
        call_bid=3, call_ask=4, put_bid=18, put_ask=18,
        lot_size=10, instrument_class="INDEX", volume=100, oi=100,
        call_bid_qty=19, call_ask_qty=45, put_bid_qty=55, put_ask_qty=40,
    )
    results = scan_box_snapshot(
        (low, high), atm_strike=100, instrument_class="INDEX",
        config=BoxSpreadScanConfig(min_executable_edge=0, max_option_spread_pct=100),
        policy=__import__("app.backtesting.arbitrage_scan_policy", fromlist=["ScanPolicy"]).ScanPolicy(index_box_distances=(1,)),
    )
    long_result = next(row for row in results if row.direction == "LONG")
    # LONG buys low CE/high PE at ask and sells high CE/low PE at bid.
    assert long_result.max_executable_lots == min(25, 19, 40, 30) // 10 == 1


def test_live_box_spread_max_lots_fails_closed_when_one_leg_depth_is_missing():
    from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot
    from app.backtesting.arbitrage_scan_policy import ScanPolicy

    low = OptionQuote(
        1_800_000_000_000_000_000, "NIFTY", 20261029, 100,
        10, 12, 8, 9, 10, "INDEX", 100, 100,
        call_bid_qty=50, call_ask_qty=25, put_bid_qty=None, put_ask_qty=60,
    )
    high = OptionQuote(
        1_800_000_000_000_000_000, "NIFTY", 20261029, 120,
        3, 4, 18, 18, 10, "INDEX", 100, 100,
        call_bid_qty=19, call_ask_qty=45, put_bid_qty=55, put_ask_qty=40,
    )
    results = scan_box_snapshot(
        (low, high), atm_strike=100, instrument_class="INDEX",
        config=BoxSpreadScanConfig(min_executable_edge=0, max_option_spread_pct=100),
        policy=ScanPolicy(index_box_distances=(1,)),
    )
    long_result = next(row for row in results if row.direction == "LONG")
    assert long_result.max_executable_lots == 0



def test_live_box_spread_short_capacity_uses_opposite_four_leg_sides():
    from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot
    from app.backtesting.arbitrage_scan_policy import ScanPolicy

    low = OptionQuote(
        1_800_000_000_000_000_000, "NIFTY", 20261029, 100,
        15, 16, 10, 11, 10, "INDEX", 100, 100,
        call_bid_qty=50, call_ask_qty=25, put_bid_qty=30, put_ask_qty=60,
    )
    high = OptionQuote(
        1_800_000_000_000_000_000, "NIFTY", 20261029, 120,
        2, 3, 25, 26, 10, "INDEX", 100, 100,
        call_bid_qty=19, call_ask_qty=45, put_bid_qty=55, put_ask_qty=40,
    )
    results = scan_box_snapshot(
        (low, high), atm_strike=100, instrument_class="INDEX",
        config=BoxSpreadScanConfig(min_executable_edge=0, max_option_spread_pct=100),
        policy=ScanPolicy(index_box_distances=(1,)),
    )
    short_result = next(row for row in results if row.direction == "SHORT")
    # SHORT sells low CE/high PE at bid and buys high CE/low PE at ask.
    assert short_result.max_executable_lots == min(50, 45, 55, 60) // 10 == 4


def test_live_box_spread_capacity_fails_closed_on_mismatched_leg_lot_sizes():
    from dataclasses import replace
    from app.scanner.box_spread import _max_executable_lots

    low = replace(
        quote(100, call_bid=10, call_ask=12, put_bid=8, put_ask=9),
        lot_size=10, call_bid_qty=50, call_ask_qty=25,
        put_bid_qty=30, put_ask_qty=60,
    )
    high = replace(
        quote(120, call_bid=3, call_ask=4, put_bid=18, put_ask=18),
        lot_size=20, call_bid_qty=19, call_ask_qty=45,
        put_bid_qty=55, put_ask_qty=40,
    )
    assert _max_executable_lots(low, high, "LONG") == 0


def test_live_box_spread_capacity_fails_closed_for_invalid_depth_quantities():
    from dataclasses import replace
    import pytest
    from app.scanner.box_spread import _max_executable_lots

    low = replace(
        quote(100, call_bid=10, call_ask=12, put_bid=8, put_ask=9),
        lot_size=10, call_bid_qty=50, call_ask_qty=25,
        put_bid_qty=30, put_ask_qty=60,
    )
    high = replace(
        quote(120, call_bid=3, call_ask=4, put_bid=18, put_ask=18),
        lot_size=10, call_bid_qty=19, call_ask_qty=45,
        put_bid_qty=55, put_ask_qty=40,
    )

    for invalid in (0, -1, 1.5, "not-a-number", None):
        broken_low = replace(low, call_ask_qty=invalid)
        broken_high = replace(high, call_ask_qty=invalid)
        assert _max_executable_lots(broken_low, high, "LONG") == 0
        assert _max_executable_lots(low, broken_high, "SHORT") == 0


def test_live_box_spread_capacity_fails_closed_for_invalid_direction():
    from app.scanner.box_spread import _max_executable_lots

    low = quote(100, call_bid=10, call_ask=12, put_bid=8, put_ask=9)
    high = quote(120, call_bid=3, call_ask=4, put_bid=18, put_ask=18)
    assert _max_executable_lots(low, high, "UNKNOWN") == 0
