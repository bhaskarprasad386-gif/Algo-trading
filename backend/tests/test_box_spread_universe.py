from app.backtesting.arbitrage_backtester import OptionQuote
from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot


def test_box_scanner_accepts_explicit_non_nifty50_stock_universe():
    quotes = (
        OptionQuote(1, "ABC", 20260930, 100.0, 10.0, 10.2, 1.0, 1.02, 10, "STOCK", 100, 100),
        OptionQuote(1, "ABC", 20260930, 110.0, 8.0, 9.0, 2.0, 3.0, 10, "STOCK", 100, 100),
    )
    results = scan_box_snapshot(
        quotes,
        atm_strike=100.0,
        instrument_class="STOCK",
        config=BoxSpreadScanConfig(
            allowed_stock_symbols=frozenset({"ABC"}),
        ),
        policy=__import__("app.backtesting.arbitrage_scan_policy", fromlist=["ScanPolicy"]).ScanPolicy(stock_box_distances=(1,)),
    )
    assert results
    assert all(r.low.underlying == "ABC" for r in results)


def test_box_uses_liquid_non_contiguous_strikes():
    quotes = (
        OptionQuote(1, "ABC", 20260930, 100.0, 10.0, 10.2, 1.0, 1.02, 10, "STOCK", 100, 100),
        OptionQuote(1, "ABC", 20260930, 105.0, 10.0, 20.0, 1.0, 5.0, 10, "STOCK", 100, 100),
        OptionQuote(1, "ABC", 20260930, 115.0, 8.0, 8.2, 2.0, 2.02, 10, "STOCK", 100, 100),
    )
    results = scan_box_snapshot(
        quotes,
        atm_strike=100.0,
        instrument_class="STOCK",
        config=BoxSpreadScanConfig(
            allowed_stock_symbols=frozenset({"ABC"}),
            max_option_spread_pct=5.0,
        ),
        policy=__import__("app.backtesting.arbitrage_scan_policy", fromlist=["ScanPolicy"]).ScanPolicy(stock_box_distances=(2,)),
    )
    assert results
    assert any(r.low.strike == 100.0 and r.high.strike == 115.0 for r in results)
    assert all(not (r.low.strike == 105.0 or r.high.strike == 105.0) for r in results)


def test_box_rejects_illiquid_volume_oi_and_wide_spread():
    quotes = (
        OptionQuote(1, "ABC", 20260930, 100.0, 10.0, 10.2, 1.0, 1.02, 10, "STOCK", 100, 100),
        OptionQuote(1, "ABC", 20260930, 110.0, 8.0, 8.2, 2.0, 2.02, 10, "STOCK", 0, 100),
        OptionQuote(1, "ABC", 20260930, 120.0, 7.0, 7.2, 2.0, 2.02, 10, "STOCK", 100, 0),
        OptionQuote(1, "ABC", 20260930, 130.0, 6.0, 6.6, 1.0, 1.02, 10, "STOCK", 100, 100),
    )
    results = scan_box_snapshot(
        quotes,
        atm_strike=100.0,
        instrument_class="STOCK",
        config=BoxSpreadScanConfig(max_option_spread_pct=5.0),
        policy=__import__("app.backtesting.arbitrage_scan_policy", fromlist=["ScanPolicy"]).ScanPolicy(stock_box_distances=(3,)),
    )
    assert not results


def test_box_requires_both_call_and_put_to_be_liquid():
    quotes = (
        OptionQuote(1, "ABC", 20260930, 100.0, 10.0, 10.2, 1.0, 1.02, 10, "STOCK", 100, 100),
        OptionQuote(1, "ABC", 20260930, 110.0, 8.0, 8.2, 2.0, 3.0, 10, "STOCK", 100, 100),
    )
    results = scan_box_snapshot(
        quotes,
        atm_strike=100.0,
        instrument_class="STOCK",
        config=BoxSpreadScanConfig(max_option_spread_pct=5.0),
        policy=__import__("app.backtesting.arbitrage_scan_policy", fromlist=["ScanPolicy"]).ScanPolicy(stock_box_distances=(1,)),
    )
    assert not results
