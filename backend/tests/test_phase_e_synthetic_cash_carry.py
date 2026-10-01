from app.backtesting.arbitrage_backtester import FutureQuote, OptionQuote
from app.backtesting.arbitrage_scan_policy import ScanPolicy
from app.scanner.synthetic_cash_carry import SyntheticScanConfig, scan_synthetic_snapshot


def option(ts, strike):
    return OptionQuote(ts, "NIFTY", 20270101, strike, 9.0, 11.0, 4.0, 6.0, 50, "INDEX", 100, 1000)


def test_synthetic_index_scope_is_atm_plus_minus_ten():
    p = ScanPolicy()
    assert p.stock_synthetic_radius == 7
    assert p.index_synthetic_radius == 10


def test_synthetic_uses_executable_bid_ask_sides():
    ts = 1_000_000_000
    opts = [option(ts, float(k)) for k in range(90, 111)]
    fut = FutureQuote(ts, "NIFTY", 20270101, 106.0, 108.0, 50, "INDEX", 100, 1000)
    results = scan_synthetic_snapshot(opts, fut, atm_strike=100.0,
                                      config=SyntheticScanConfig())
    assert results
    # LONG synthetic compares executable future bid with synthetic buy:
    # strike + CE ask - PE bid = 95 for strike 90, so edge is 9.
    long90 = next(r for r in results if r.strike_distance == 10 and r.strike_side == "LOWER" and r.direction == "LONG")
    assert long90.executable_edge == 9.0


def test_synthetic_threshold_filters_before_results():
    ts = 1_000_000_000
    opts = [option(ts, float(k)) for k in range(99, 102)]
    fut = FutureQuote(ts, "NIFTY", 20270101, 115.0, 116.0, 50, "INDEX", 100, 1000)
    assert scan_synthetic_snapshot(opts, fut, atm_strike=100.0,
        config=SyntheticScanConfig(min_executable_edge=9.0))
    assert not scan_synthetic_snapshot(opts, fut, atm_strike=100.0,
        config=SyntheticScanConfig(min_executable_edge=9.1))


def test_synthetic_rejects_timestamp_mismatch():
    fut = FutureQuote(1_000_000_000, "NIFTY", 20270101, 104.0, 106.0, 50, "INDEX", 100, 1000)
    bad = option(1_000_000_001, 100.0)
    try:
        scan_synthetic_snapshot([bad], fut, atm_strike=100.0)
    except ValueError:
        return
    raise AssertionError("timestamp mismatch must be rejected")


def test_stock_requires_nifty50_universe():
    fut = FutureQuote(1_000_000_000, "RELIANCE", 20270101, 104.0, 106.0, 250, "STOCK", 100, 1000)
    try:
        scan_synthetic_snapshot([OptionQuote(1_000_000_000, "RELIANCE", 20270101, 100, 9, 11, 4, 6, 250, "STOCK", 100, 1000)], fut, atm_strike=100)
    except ValueError:
        return
    raise AssertionError("stock scan must require NIFTY-50 universe")
