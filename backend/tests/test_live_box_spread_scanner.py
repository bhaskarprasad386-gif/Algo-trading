from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner


def test_box_scanner_does_not_mix_call_and_put_expiries():
    scanner = LiveBoxSpreadScanner(atm_provider=lambda _s, _t: 100, config_provider=lambda _s: __import__("app.scanner.box_spread", fromlist=["BoxSpreadScanConfig"]).BoxSpreadScanConfig(allowed_stock_symbols=frozenset({"ABC"})), policy=__import__("app.backtesting.arbitrage_scan_policy", fromlist=["ScanPolicy"]).ScanPolicy(stock_box_distances=(1,)))
    common = {
        "underlying": "ABC",
        "instrument_class": "STOCK",
        "source_timestamp_ns": 1,
        "strike": 100,
        "bid": 10,
        "ask": 11,
        "lot_size": 10,
        "volume": 100,
        "oi": 100,
    }
    assert scanner.observe(common | {"option_type": "CE", "expiry": "30SEP2026"}) == ()
    assert scanner.observe(common | {"option_type": "PE", "expiry": "31OCT2026"}) == ()


def test_box_scanner_assembles_matching_expiry_call_put():
    scanner = LiveBoxSpreadScanner(
        atm_provider=lambda _s, _t: 100,
        config_provider=lambda _s: __import__("app.scanner.box_spread", fromlist=["BoxSpreadScanConfig"]).BoxSpreadScanConfig(allowed_stock_symbols=frozenset({"ABC"})),
    )
    base = {
        "underlying": "ABC",
        "instrument_class": "STOCK",
        "source_timestamp_ns": 2,
        "expiry": "30SEP2026",
        "strike": 100,
        "bid": 10,
        "ask": 11,
        "lot_size": 10,
        "volume": 100,
        "oi": 100,
    }
    assert scanner.observe(base | {"option_type": "CE"}) == ()
    assert scanner.observe(base | {"option_type": "PE"}) == ()
