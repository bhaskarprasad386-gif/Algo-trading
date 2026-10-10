from app.scanner.live_synthetic_scanner import LiveSyntheticScanner


def _base(ts, strike, typ, bid, ask):
    return {
        "underlying": "NIFTY",
        "instrument_class": "INDEX",
        "option_type": typ,
        "expiry": "30SEP2026",
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "lot_size": 1,
        "source_timestamp_ns": ts,
        "symbol": f"NIFTY{strike}{typ}",
        "bid_qty": 10,
        "ask_qty": 10,
        "volume": 100,
        "oi": 1000,
    }


def test_live_synthetic_scanner_assembles_real_ce_pe_and_future():
    ts = 1_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner.observe(_base(ts, 100.0, "CE", 4.0, 5.0))
    scanner.observe(_base(ts, 100.0, "PE", 4.0, 5.0))
    scanner.observe(_base(ts, 105.0, "CE", 4.0, 5.0))
    scanner.observe(_base(ts, 105.0, "PE", 4.0, 5.0))
    result = scanner.observe(
        {
            "underlying": "NIFTY",
            "instrument_class": "INDEX",
            "option_type": "",
            "expiry": "30SEP2026",
            "bid": 115.0,
            "ask": 116.0,
            "bid_qty": 10,
            "ask_qty": 10,
            "lot_size": 1,
            "source_timestamp_ns": ts,
            "symbol": "NIFTYFUT",
        }
    )
    assert result
    assert result[0].option.strike == 105.0
    assert result[0].future.bid == 115.0
    assert result[0].option.call_timestamp_ns == ts
    assert result[0].option.put_timestamp_ns == ts
    assert result[0].future.source_timestamp_ns == ts


def test_live_synthetic_scanner_rejects_missing_timestamp():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    assert scanner.observe({"underlying": "NIFTY", "instrument_class": "INDEX", "bid": 1, "ask": 2}) == ()


def test_live_synthetic_scanner_rejects_mismatched_option_expiry():
    ts = 2_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner.observe(_base(ts, 105.0, "CE", 4.0, 5.0))
    scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": "07OCT2026"})
    result = scanner.observe(
        {
            "underlying": "NIFTY",
            "instrument_class": "INDEX",
            "option_type": "",
            "expiry": "30SEP2026",
            "bid": 115.0,
            "ask": 116.0,
            "lot_size": 1,
            "source_timestamp_ns": ts,
            "symbol": "NIFTYFUT",
        }
    )
    assert result == ()


def test_live_synthetic_scanner_rejects_mixed_expiry_legs_even_when_both_legs_exist():
    ts = 3_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner.observe(_base(ts, 105.0, "CE", 4.0, 5.0))
    scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": "07OCT2026"})
    result = scanner.observe(
        {
            "underlying": "NIFTY",
            "instrument_class": "INDEX",
            "option_type": "",
            "expiry": "30SEP2026",
            "bid": 115.0,
            "ask": 116.0,
            "lot_size": 1,
            "source_timestamp_ns": ts,
            "symbol": "NIFTYFUT",
        }
    )
    assert result == ()


def test_live_synthetic_scanner_rejects_illiquid_strike():
    ts = 4_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner.observe(_base(ts, 105.0, "CE", 4.0, 5.0))
    scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "volume": 0, "oi": 0})
    result = scanner.observe(
        {
            "underlying": "NIFTY",
            "instrument_class": "INDEX",
            "option_type": "",
            "expiry": "30SEP2026",
            "bid": 115.0,
            "ask": 116.0,
            "bid_qty": 10,
            "ask_qty": 10,
            "lot_size": 1,
            "source_timestamp_ns": ts,
            "symbol": "NIFTYFUT",
            "volume": 100,
            "oi": 1000,
        }
    )
    assert result == ()


def test_live_synthetic_scanner_keeps_current_and_near_expiries_isolated():
    ts = 5_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    for exp, fut in (("30SEP2026", 115.0), ("07OCT2026", 116.0)):
        scanner.observe({**_base(ts, 100.0, "CE", 4.0, 5.0), "expiry": exp})
        scanner.observe({**_base(ts, 100.0, "PE", 4.0, 5.0), "expiry": exp})
        scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": exp})
        scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": exp})
        result = scanner.observe({"underlying":"NIFTY","instrument_class":"INDEX","option_type":"",
            "expiry":exp,"bid":fut,"ask":fut+1,"bid_qty":10,"ask_qty":10,"lot_size":1,"source_timestamp_ns":ts,
            "symbol":"NIFTYFUT","volume":100,"oi":1000})
        assert result
        assert {r.future.expiry for r in result} == {r.option.expiry for r in result}

def test_live_synthetic_scanner_does_not_cross_second_boundaries():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    ts = 5_999_999_999
    # Keep a complete ATM pair in the same timestamp bucket so the assertion
    # reaches the pairing guard; only the 105-strike CE/PE are split by >1ns.
    scanner.observe(_base(ts, 100.0, "CE", 4.0, 5.0))
    scanner.observe(_base(ts, 100.0, "PE", 4.0, 5.0))
    scanner.observe(_base(ts, 105.0, "CE", 4.0, 5.0))
    scanner.observe(_base(ts + 2_000_000_000, 105.0, "PE", 4.0, 5.0))
    result = scanner.observe({"underlying":"NIFTY","instrument_class":"INDEX","option_type":"",
        "expiry":"30SEP2026","bid":115,"ask":116,"bid_qty":10,"ask_qty":10,"lot_size":1,"source_timestamp_ns":ts+2_000_000_000,"symbol":"NIFTYFUT"})
    assert result == ()


def test_live_synthetic_scanner_allows_synchronized_ticks_across_second_boundary():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    ts = 5_999_999_999
    scanner.observe(_base(ts, 100.0, "CE", 4.0, 5.0))
    scanner.observe(_base(ts, 100.0, "PE", 4.0, 5.0))
    scanner.observe(_base(ts, 105.0, "CE", 4.0, 5.0))
    scanner.observe(_base(ts + 2, 105.0, "PE", 4.0, 5.0))
    result = scanner.observe({"underlying":"NIFTY","instrument_class":"INDEX","option_type":"", "expiry":"30SEP2026", "bid":115,"ask":116,"bid_qty":10,"ask_qty":10,"lot_size":1,"source_timestamp_ns":ts+2,"symbol":"NIFTYFUT","volume":100,"oi":1000})
    assert result
    assert result[0].option.timestamp_ns == ts + 2
    assert result[0].future.timestamp_ns == ts + 2


def test_live_synthetic_scanner_rejects_future_without_executable_depth():
    ts = 7_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner.observe(_base(ts, 105.0, "CE", 4.0, 5.0))
    scanner.observe(_base(ts, 105.0, "PE", 4.0, 5.0))
    result = scanner.observe({"underlying":"NIFTY","instrument_class":"INDEX","option_type":"",
        "expiry":"30SEP2026","bid":115,"ask":116,"lot_size":1,"source_timestamp_ns":ts,
        "symbol":"NIFTYFUT","volume":100,"oi":1000})
    assert result == ()


def test_live_synthetic_scanner_keeps_fresh_pair_when_unrelated_strike_is_older():
    ts = 8_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner.observe(_base(ts, 105.0, "CE", 4.0, 5.0))
    scanner.observe(_base(ts + 400_000_000, 105.0, "PE", 4.0, 5.0))
    scanner.observe(_base(ts - 700_000_000, 110.0, "CE", 4.0, 5.0))
    result = scanner.observe({"underlying":"NIFTY","instrument_class":"INDEX","option_type":"",
        "expiry":"30SEP2026","bid":115,"ask":116,"bid_qty":10,"ask_qty":10,
        "lot_size":1,"source_timestamp_ns":ts + 700_000_000,"symbol":"NIFTYFUT","volume":100,"oi":1000})
    assert result
    assert result[0].option.call_timestamp_ns == ts
    assert result[0].option.put_timestamp_ns == ts + 400_000_000


def test_live_synthetic_scanner_rejects_commodity_scope():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    payload = _base(6_000_000_000, 105.0, "CE", 4.0, 5.0)
    payload["instrument_class"] = "COMMODITY"
    assert scanner.observe(payload) == ()
