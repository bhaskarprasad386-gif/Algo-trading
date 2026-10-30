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

def test_box_scanner_rejects_malformed_timestamp_and_nonfinite_quotes():
    scanner = LiveBoxSpreadScanner(atm_provider=lambda _s, _t: 100)
    base = {
        "underlying": "ABC",
        "instrument_class": "STOCK",
        "source_timestamp_ns": "not-a-timestamp",
        "expiry": "30SEP2026",
        "strike": 100,
        "option_type": "CE",
        "bid": 10,
        "ask": 11,
    }

    assert scanner.observe(base) == ()
    assert scanner.diagnostics_snapshot()["counts"]["invalid_identity_or_timestamp"] == 1

    base["source_timestamp_ns"] = 1
    base["bid"] = float("inf")
    assert scanner.observe(base) == ()
    assert scanner.diagnostics_snapshot()["counts"]["invalid_bid_quote"] == 1


def test_box_scanner_caps_active_buckets_and_counts_pruning():
    scanner = LiveBoxSpreadScanner(atm_provider=lambda _s, _t: None)
    scanner.MAX_ACTIVE_BUCKETS = 2
    base = {
        "instrument_class": "STOCK",
        "source_timestamp_ns": 1,
        "expiry": "30SEP2026",
        "strike": 100,
        "option_type": "CE",
        "bid": 10,
        "ask": 11,
    }

    for symbol, timestamp in (("AAA", 1), ("BBB", 2), ("CCC", 3)):
        scanner.observe(base | {"underlying": symbol, "source_timestamp_ns": timestamp})

    snapshot = scanner.diagnostics_snapshot()
    assert snapshot["active_buckets"] == 2
    assert snapshot["counts"]["pruned_buckets"] == 1
    assert {key[0] for key in scanner._buckets} == {"BBB", "CCC"}


def test_box_scanner_preserves_top_of_book_bid_ask_quantities():
    from app.backtesting.arbitrage_scan_policy import ScanPolicy
    from app.scanner.box_spread import BoxSpreadScanConfig

    scanner = LiveBoxSpreadScanner(
        atm_provider=lambda _s, _t: 100,
        config_provider=lambda _s: BoxSpreadScanConfig(allowed_stock_symbols=frozenset({"ABC"})),
        policy=ScanPolicy(stock_box_distances=(1,)),
    )
    results = ()
    for strike in (100, 110):
        for option_type, bid_qty, ask_qty in (("CE", 10 + strike, 20 + strike), ("PE", 30 + strike, 40 + strike)):
            results = scanner.observe({
                "underlying": "ABC", "instrument_class": "STOCK", "source_timestamp_ns": 10,
                "expiry": "30SEP2026", "strike": strike, "option_type": option_type,
                "bid": 10, "ask": 10.1, "bid_qty": bid_qty, "ask_qty": ask_qty,
                "lot_size": 10, "volume": 100, "oi": 100,
            }) or results

    assert results
    low = results[0].low
    high = results[0].high
    assert low.call_bid_qty == 110 and low.call_ask_qty == 120
    assert low.put_bid_qty == 130 and low.put_ask_qty == 140
    assert high.call_bid_qty == 120 and high.call_ask_qty == 130
    assert high.put_bid_qty == 140 and high.put_ask_qty == 150



def test_monitor_scan_retains_zero_and_negative_edges_for_existing_positions():
    from app.backtesting.arbitrage_backtester import OptionQuote
    from app.backtesting.arbitrage_scan_policy import ScanPolicy
    from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot

    low = OptionQuote(
        1, "ABC", 20261231, 100, 9, 10, 5, 6, 10, "STOCK", 100, 100,
    )
    high = OptionQuote(
        1, "ABC", 20261231, 110, 5, 6, 9, 10, 10, "STOCK", 100, 100,
    )
    results = scan_box_snapshot(
        (low, high), atm_strike=100, instrument_class="STOCK",
        config=BoxSpreadScanConfig(
            min_executable_edge=-100, allowed_stock_symbols=frozenset({"ABC"}),
            max_option_spread_pct=100,
        ),
        policy=ScanPolicy(stock_box_distances=(1,)),
        include_nonpositive=True,
    )
    by_direction = {row.direction: row.executable_edge for row in results}
    assert by_direction["LONG"] == 0
    assert by_direction["SHORT"] == -4


def test_normal_opportunity_scan_still_excludes_nonpositive_edges():
    from app.backtesting.arbitrage_backtester import OptionQuote
    from app.backtesting.arbitrage_scan_policy import ScanPolicy
    from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot

    low = OptionQuote(1, "ABC", 20261231, 100, 9, 10, 5, 6, 10, "STOCK", 100, 100)
    high = OptionQuote(1, "ABC", 20261231, 110, 5, 6, 9, 10, 10, "STOCK", 100, 100)
    results = scan_box_snapshot(
        (low, high), atm_strike=100, instrument_class="STOCK",
        config=BoxSpreadScanConfig(
            min_executable_edge=-100, allowed_stock_symbols=frozenset({"ABC"}),
            max_option_spread_pct=100,
        ),
        policy=ScanPolicy(stock_box_distances=(1,)),
    )
    assert results == ()



def test_position_monitor_includes_far_strike_pairs_outside_normal_scan_policy():
    from app.backtesting.arbitrage_backtester import OptionQuote
    from app.backtesting.arbitrage_scan_policy import ScanPolicy
    from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot

    quotes = []
    for strike in (100, 110, 120):
        quotes.append(OptionQuote(
            1, "ABC", 20261231, strike, 10, 10.1, 10, 10.1,
            10, "STOCK", 100, 100,
        ))
    config = BoxSpreadScanConfig(
        min_executable_edge=-100, allowed_stock_symbols=frozenset({"ABC"}),
        max_option_spread_pct=100,
    )
    policy = ScanPolicy(stock_box_distances=(1,))
    normal = scan_box_snapshot(
        quotes, atm_strike=100, instrument_class="STOCK",
        config=config, policy=policy, include_nonpositive=True,
    )
    monitor = scan_box_snapshot(
        quotes, atm_strike=100, instrument_class="STOCK",
        config=config, policy=policy, include_nonpositive=True,
        include_all_strike_pairs=True,
    )
    assert (100, 120) not in {(int(row.low.strike), int(row.high.strike)) for row in normal}
    assert (100, 120) in {(int(row.low.strike), int(row.high.strike)) for row in monitor}
