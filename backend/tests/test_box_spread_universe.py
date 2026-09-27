from app.backtesting.arbitrage_backtester import OptionQuote
from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot


def test_box_scanner_accepts_explicit_non_nifty50_stock_universe():
    quotes = (
        OptionQuote(1, "ABC", 20260930, 100.0, 10.0, 11.0, 1.0, 2.0, 10, "STOCK", 100, 100),
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
