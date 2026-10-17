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


def test_index_scan_keeps_fifteen_position_radius():
    strikes = tuple(float(x) for x in range(100, 201))
    quotes = tuple(option(1, strike) for strike in strikes)
    results = scan_synthetic_snapshot(
        quotes, future(1, bid=170, ask=171), atm_strike=150,
        config=SyntheticScanConfig(min_executable_edge=0.0),
    )
    assert results
    assert max(r.strike_distance for r in results) == 15
    assert all(r.strike_distance <= 15 for r in results)


def test_scanner_ranks_highest_executable_edge_first():
    quotes = (
        option(1, 95, cb=20, ca=21, pb=1, pa=2),
        option(1, 100, cb=8, ca=9, pb=6, pa=7),
        option(1, 105, cb=10, ca=11, pb=5, pa=6),
    )
    results = scan_synthetic_snapshot(
        quotes, future(1, bid=130, ask=131), atm_strike=100,
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
        assert "top-15 liquid" in str(exc)
    else:
        raise AssertionError("expected top-15 liquid universe rejection")


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



def test_index_arbitrage_requires_strictly_more_than_ten_points():
    quote = option(1, 99)
    assert not scan_synthetic_snapshot(
        (quote,), future(1, bid=112, ask=113), atm_strike=100,
    )
    results = scan_synthetic_snapshot(
        (quote,), future(1, bid=112.01, ask=113.01), atm_strike=100,
    )
    assert results
    assert all(result.executable_edge > 10 for result in results)


def test_any_configured_top15_stock_can_qualify_above_five_points():
    stock_option = OptionQuote(
        1, "RELIANCE", 20261231, 99, 8, 9, 6, 7,
        lot_size=10, instrument_class="STOCK", volume=100, oi=100,
    )
    exact_threshold_future = FutureQuote(
        1, "RELIANCE", 20261231, 107, 108,
        lot_size=10, instrument_class="STOCK", volume=100, oi=100,
    )
    above_threshold_future = FutureQuote(
        1, "RELIANCE", 20261231, 107.01, 108.01,
        lot_size=10, instrument_class="STOCK", volume=100, oi=100,
    )
    config = SyntheticScanConfig(allowed_stock_symbols=frozenset({"RELIANCE"}))
    assert not scan_synthetic_snapshot(
        (stock_option,), exact_threshold_future, atm_strike=100, config=config,
    )
    results = scan_synthetic_snapshot(
        (stock_option,), above_threshold_future, atm_strike=100, config=config,
    )
    assert results
    assert all(result.executable_edge > 5 for result in results)



def test_min_executable_edge_rejects_non_finite_and_non_numeric_values():
    import pytest

    for value in (float("nan"), float("inf"), float("-inf"), True, "10"):
        with pytest.raises(ValueError, match="min_executable_edge must be finite"):
            SyntheticScanConfig(min_executable_edge=value)


def test_min_executable_edge_rejects_negative_values():
    import pytest

    with pytest.raises(ValueError, match="min_executable_edge must be non-negative"):
        SyntheticScanConfig(min_executable_edge=-0.01)
