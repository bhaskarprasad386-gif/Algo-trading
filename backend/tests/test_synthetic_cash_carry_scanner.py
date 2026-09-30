from app.backtesting.arbitrage_backtester import FutureQuote, OptionQuote
from app.scanner.synthetic_cash_carry import (
    SyntheticScanConfig,
    scan_synthetic_snapshot,
)


def option(ts, strike, cb=8, ca=9, pb=6, pa=7):
    return OptionQuote(
        ts, "NIFTY", 20261231, strike, cb, ca, pb, pa,
        lot_size=10, instrument_class="INDEX", volume=100, oi=100,
    )


def future(ts, bid=110, ask=111):
    return FutureQuote(
        ts, "NIFTY", 20261231, bid, ask,
        lot_size=10, instrument_class="INDEX", volume=100, oi=100,
    )


def test_index_scan_keeps_locked_ten_position_radius():
    strikes = tuple(float(x) for x in range(100, 201))
    quotes = tuple(option(1, strike) for strike in strikes)
    results = scan_synthetic_snapshot(
        quotes, future(1, bid=170, ask=171), atm_strike=150,
        config=SyntheticScanConfig(min_executable_edge=0.0),
    )
    assert results
    assert max(r.strike_distance for r in results) == 10
    assert all(r.strike_distance <= 10 for r in results)


def test_scanner_ranks_highest_executable_edge_first():
    quotes = (
        option(1, 95, cb=20, ca=21, pb=1, pa=2),
        option(1, 100, cb=8, ca=9, pb=6, pa=7),
        option(1, 105, cb=10, ca=11, pb=5, pa=6),
    )
    results = scan_synthetic_snapshot(
        quotes, future(1, bid=120, ask=121), atm_strike=100,
    )
    assert results
    assert all(
        results[i].executable_edge >= results[i + 1].executable_edge
        for i in range(len(results) - 1)
    )


def test_stock_scan_requires_configured_nifty50_universe():
    stock_option = OptionQuote(
        1, "ABC", 20261231, 100, 8, 9, 6, 7,
        lot_size=10, instrument_class="STOCK", volume=100, oi=100,
    )
    stock_future = FutureQuote(
        1, "ABC", 20261231, 110, 111,
        lot_size=10, instrument_class="STOCK", volume=100, oi=100,
    )
    try:
        scan_synthetic_snapshot(
            (stock_option,), stock_future, atm_strike=100,
        )
    except ValueError as exc:
        assert "NIFTY-50" in str(exc)
    else:
        raise AssertionError("expected NIFTY-50 universe rejection")


def test_stock_scan_accepts_only_configured_universe_symbol():
    option = OptionQuote(
        1, "ABC", 20261231, 95, 8, 9, 6, 7,
        lot_size=10, instrument_class="STOCK", volume=100, oi=100,
    )
    atm = OptionQuote(
        1, "ABC", 20261231, 100, 8, 9, 6, 7,
        lot_size=10, instrument_class="STOCK", volume=100, oi=100,
    )
    future_quote = FutureQuote(
        1, "ABC", 20261231, 110, 111,
        lot_size=10, instrument_class="STOCK", volume=100, oi=100,
    )
    config = SyntheticScanConfig(allowed_stock_symbols=frozenset({"ABC"}))
    assert scan_synthetic_snapshot(
        (option, atm), future_quote, atm_strike=100, config=config,
    )


def test_scanner_rejects_mixed_timestamp_snapshot():
    quotes = (option(1, 95), option(2, 105))
    try:
        scan_synthetic_snapshot(quotes, future(1), atm_strike=100)
    except ValueError as exc:
        assert "timestamp" in str(exc)
    else:
        raise AssertionError("expected timestamp rejection")
