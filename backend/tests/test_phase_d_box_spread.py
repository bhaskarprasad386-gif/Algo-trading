from app.backtesting.arbitrage_backtester import OptionQuote
from app.scanner.box_spread import BOX_SCAN_POLICY, BoxSpreadScanConfig, scan_box_snapshot


def q(ts, strike, *, underlying="NIFTY", cls="INDEX", expiry=20270101, lot=50, edge=True):
    return OptionQuote(
        ts, underlying, expiry, strike,
        2.0 if edge else 10.0, 2.0,
        2.0, 2.0,
        lot, cls, 100, 1000,
    )


def make_chain(distances, cls="INDEX", underlying="NIFTY"):
    strikes = [100.0]
    for d in distances:
        strikes.extend([100.0 - d, 100.0 + d])
    return [q(1_000_000_000, s, underlying=underlying, cls=cls, lot=50) for s in sorted(set(strikes))]


def test_box_policy_is_locked_to_requested_scope():
    assert BOX_SCAN_POLICY.stock_box_distances == (1, 2, 3, 4, 5)
    assert BOX_SCAN_POLICY.index_box_distances == tuple(range(1, 11))


def test_index_scanner_reaches_ten_but_not_eleven():
    results = scan_box_snapshot(
        make_chain(range(1, 11)),
        atm_strike=100,
        instrument_class="INDEX",
    )
    distances = {r.strike_distance for r in results}
    assert 10 in distances
    assert 11 not in distances


def test_stock_scanner_stops_at_five():
    results = scan_box_snapshot(
        make_chain(range(1, 6), cls="STOCK", underlying="RELIANCE"),
        atm_strike=100,
        instrument_class="STOCK",
        config=BoxSpreadScanConfig(allowed_stock_symbols=frozenset({"RELIANCE"})),
    )
    distances = {r.strike_distance for r in results}
    assert 5 in distances
    assert 6 not in distances


def test_min_arbitrage_points_filters_before_result():
    chain = make_chain((1,), cls="INDEX")
    assert scan_box_snapshot(chain, atm_strike=100, instrument_class="INDEX",
                             config=BoxSpreadScanConfig(min_executable_edge=2.0))
    assert not scan_box_snapshot(chain, atm_strike=100, instrument_class="INDEX",
                                 config=BoxSpreadScanConfig(min_executable_edge=2.01))


def test_crossed_quote_is_rejected():
    chain = make_chain((1,))
    bad = chain[0]
    bad = OptionQuote(
        bad.timestamp_ns, bad.underlying, bad.expiry, bad.strike,
        3.0, 2.0, bad.put_bid, bad.put_ask, bad.lot_size,
        bad.instrument_class, bad.volume, bad.oi,
    )
    try:
        scan_box_snapshot([bad, *chain[1:]], atm_strike=100, instrument_class="INDEX")
    except ValueError:
        return
    raise AssertionError("crossed quote must be rejected")


def test_timestamp_mismatch_is_rejected():
    chain = make_chain((1,))
    x = chain[0]
    mismatched = OptionQuote(
        x.timestamp_ns + 1, x.underlying, x.expiry, x.strike,
        x.call_bid, x.call_ask, x.put_bid, x.put_ask, x.lot_size,
        x.instrument_class, x.volume, x.oi,
    )
    try:
        scan_box_snapshot([mismatched, *chain[1:]], atm_strike=100, instrument_class="INDEX")
    except ValueError:
        return
    raise AssertionError("timestamp mismatch must be rejected")
