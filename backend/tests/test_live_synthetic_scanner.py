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
            "lot_size": 1,
            "source_timestamp_ns": ts,
            "symbol": "NIFTYFUT",
        }
    )
    assert result
    assert result[0].option.strike == 105.0
    assert result[0].future.bid == 115.0


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
