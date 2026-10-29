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


def test_box_scanner_keeps_current_and_near_expiries_in_separate_buckets():
    from app.backtesting.arbitrage_scan_policy import ScanPolicy
    from app.scanner.box_spread import BoxSpreadScanConfig
    scanner = LiveBoxSpreadScanner(atm_provider=lambda _s, _t: 100, config_provider=lambda _s: BoxSpreadScanConfig(allowed_stock_symbols=frozenset({"ABC"})), policy=ScanPolicy(stock_box_distances=(1,)))
    for expiry in ("30SEP2026", "31OCT2026"):
        for strike in (100, 110):
            base = {"underlying":"ABC","instrument_class":"STOCK","source_timestamp_ns":10,"expiry":expiry,"strike":strike,"bid":10,"ask":11,"lot_size":10,"volume":100,"oi":100}
            scanner.observe(base | {"option_type":"CE"})
            scanner.observe(base | {"option_type":"PE"})
    assert all(key[2] in (20260930, 20261031) for key in scanner._buckets)



def test_box_scanner_counts_missing_and_invalid_quote_reasons():
    scanner = LiveBoxSpreadScanner(atm_provider=lambda _s, _t: 100)
    base = {
        "underlying": "ABC",
        "instrument_class": "STOCK",
        "source_timestamp_ns": 1,
        "expiry": "30SEP2026",
        "strike": 100,
        "option_type": "CE",
        "ask": 11,
    }

    assert scanner.observe(base) == ()
    counts = scanner.diagnostics_snapshot()["counts"]
    assert counts["missing_bid_quote"] == 1

    assert scanner.observe(base | {"bid": "not-a-price"}) == ()
    counts = scanner.diagnostics_snapshot()["counts"]
    assert counts["invalid_bid_quote"] == 1


def test_box_scanner_counts_crossed_quote_and_malformed_expiry():
    scanner = LiveBoxSpreadScanner(atm_provider=lambda _s, _t: 100)
    base = {
        "underlying": "ABC",
        "instrument_class": "STOCK",
        "source_timestamp_ns": 1,
        "expiry": "30SEP2026",
        "strike": 100,
        "option_type": "CE",
        "bid": 12,
        "ask": 11,
    }

    assert scanner.observe(base) == ()
    assert scanner.diagnostics_snapshot()["counts"]["crossed_market_quote"] == 1

    assert scanner.observe(base | {"bid": 10, "ask": 11, "expiry": "not-an-expiry"}) == ()
    assert scanner.diagnostics_snapshot()["counts"]["invalid_expiry"] == 1


def test_box_scanner_rejects_future_dated_epoch_timestamp(monkeypatch):
    import time
    scanner = LiveBoxSpreadScanner(atm_provider=lambda _s, _t: 100)
    future_ts = time.time_ns() + 10_000_000_000
    payload = {
        "underlying": "ABC",
        "instrument_class": "STOCK",
        "source_timestamp_ns": future_ts,
        "expiry": "30SEP2026",
        "strike": 100,
        "option_type": "CE",
        "bid": 10,
        "ask": 11,
    }

    assert scanner.observe(payload) == ()
    assert scanner.diagnostics_snapshot()["counts"]["future_timestamp"] == 1
