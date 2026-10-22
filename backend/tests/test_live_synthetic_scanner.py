from app.scanner.live_synthetic_scanner import LiveSyntheticScanner as _LiveSyntheticScanner
from app.scanner.synthetic_cash_carry import SyntheticScanConfig


def LiveSyntheticScanner(*args, **kwargs):
    """Use logical timestamps only in this deterministic unit-test module."""
    kwargs.setdefault("allow_logical_timestamps", True)
    return _LiveSyntheticScanner(*args, **kwargs)


def test_live_scanner_normalizes_numeric_broker_expiry_formats():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)

    assert scanner._expiry(20261029) == 20261029
    assert scanner._expiry("20261029") == 20261029
    assert scanner._expiry("20261029.0") == 20261029
    assert scanner._expiry("29OCT2026") == 20261029
    assert scanner._expiry("2026-10-29") == 20261029


def test_synthetic_pricing_config_accepts_explicit_carry_and_cost_estimate():
    config = SyntheticScanConfig(rate=0.055, fees_per_unit=1.0)
    assert config.rate == 0.055
    assert config.fees_per_unit == 1.0


def test_live_scanner_derives_time_to_expiry_and_passes_config(monkeypatch):
    import app.scanner.live_synthetic_scanner as scanner_module
    captured = {}
    def fake_scan(options, future, *, atm_strike, config, policy=None):
        captured["config"] = config
        return ()
    monkeypatch.setattr(scanner_module, "scan_synthetic_snapshot", fake_scan)
    scanner = LiveSyntheticScanner(
        atm_provider=lambda _s, _t: 100.0,
        config_provider=lambda _s: SyntheticScanConfig(rate=0.055, fees_per_unit=1.25),
    )
    import time
    from datetime import date, timedelta
    ts = time.time_ns()
    expiry = (date.today() + timedelta(days=30)).strftime("%d%b%Y").upper()
    scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({"underlying":"NIFTY", "instrument_class":"INDEX", "option_type":"",
        "expiry":expiry, "bid":117.0, "ask":118.0, "lot_size":1,
        "source_timestamp_ns":ts, "symbol":"NIFTYFUT", "volume":100, "oi":1000})
    assert captured["config"].rate == 0.055
    assert captured["config"].fees_per_unit == 1.25
    assert captured["config"].time_to_expiry_years > 0


def test_live_scanner_expiry_horizon_uses_future_quote_source_timestamp(monkeypatch):
    import app.scanner.live_synthetic_scanner as scanner_module
    from datetime import date, datetime, time as datetime_time, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Kolkata")
    source_at = datetime(2026, 10, 17, 10, 0, tzinfo=tz)
    ts = int(source_at.timestamp()) * 1_000_000_000
    monkeypatch.setattr(scanner_module.time, "time_ns", lambda: ts)
    captured = {}

    def fake_scan(options, future, *, atm_strike, config, policy=None):
        captured["config"] = config
        return ()

    monkeypatch.setattr(scanner_module, "scan_synthetic_snapshot", fake_scan)
    expiry_date = date(2026, 11, 17)
    expiry = expiry_date.strftime("%d%b%Y").upper()
    scanner = LiveSyntheticScanner(
        atm_provider=lambda _s, _t: 100.0,
        config_provider=lambda _s: SyntheticScanConfig(rate=0.055),
    )
    scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT",
    })

    expiry_at = datetime.combine(expiry_date, datetime_time(15, 30), tzinfo=tz)
    expected = (expiry_at - source_at).total_seconds() / (365.25 * 24 * 60 * 60)
    assert captured["config"].time_to_expiry_years == expected


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
            "bid": 117.0,
            "ask": 118.0,
            "lot_size": 1,
            "source_timestamp_ns": ts,
            "symbol": "NIFTYFUT",
        }
    )
    assert result
    assert result[0].option.strike == 105.0
    assert result[0].future.bid == 117.0


def test_live_synthetic_scanner_rejects_cross_class_option_future_triplet(monkeypatch):
    import app.scanner.live_synthetic_scanner as scanner_module

    scan_calls = []
    monkeypatch.setattr(
        scanner_module,
        "scan_synthetic_snapshot",
        lambda *args, **kwargs: scan_calls.append((args, kwargs)) or (),
    )
    ts = 1_500_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    expiry = "30SEP2026"
    ce = {**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": expiry, "instrument_class": "STOCK"}
    pe = {**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": expiry, "instrument_class": "STOCK"}
    future = {
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT",
    }

    scanner.observe(ce)
    scanner.observe(pe)
    assert scanner.observe(future) == ()

    counters = scanner.diagnostics_snapshot()["counters"]
    assert counters["instrument_class_mismatch"] == 1
    assert counters.get("scan_evaluations", 0) == 0
    assert scan_calls == []
    row = next(r for r in scanner.pair_monitor_snapshot(limit=10)["data"] if r["strike"] == 105.0)
    assert row["state"] == "INCOMPLETE"
    assert "INSTRUMENT_CLASS_MISMATCH" in row["reason"]


def test_live_synthetic_scanner_rejects_fractional_executable_depth():
    for field, value in (("bid_qty", 0.5), ("ask_qty", 1.5)):
        ts = 1_600_000_000
        scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
        expiry = "30SEP2026"
        ce = {**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": expiry}
        pe = {**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": expiry}
        ce[field] = value
        future = {
            "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
            "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
            "source_timestamp_ns": ts, "symbol": "NIFTYFUT",
        }

        scanner.observe(ce)
        scanner.observe(pe)
        assert scanner.observe(future) == ()
        counters = scanner.diagnostics_snapshot()["counters"]
        assert counters["illiquid_or_incomplete_option_leg"] >= 1
        assert counters.get("scan_evaluations", 0) == 0

        row = next(r for r in scanner.pair_monitor_snapshot(limit=10)["data"] if r["strike"] == 105.0)
        assert row["state"] == "INCOMPLETE"
        assert ("CALL_DEPTH_MISSING" if field == "bid_qty" else "CALL_DEPTH_MISSING") in row["reason"]


def test_live_synthetic_scanner_rejects_missing_timestamp():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    assert scanner.observe({"underlying": "NIFTY", "instrument_class": "INDEX", "bid": 1, "ask": 2}) == ()


def test_live_scanner_pairs_real_quotes_within_two_second_exchange_skew():
    import time
    from datetime import date, timedelta
    ts = time.time_ns()
    expiry = (date.today() + timedelta(days=7)).strftime("%d%b%Y").upper()
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "CE",
        "expiry": expiry, "strike": 105.0, "bid": 10.0, "ask": 11.0, "bid_qty": 10, "ask_qty": 10,
        "lot_size": 1, "source_timestamp_ns": ts, "symbol": "NIFTY29OCT26105CE",
        "volume": 100, "oi": 1000,
    })
    scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "PE",
        "expiry": expiry, "strike": 105.0, "bid": 4.0, "ask": 5.0, "bid_qty": 10, "ask_qty": 10,
        "lot_size": 1, "source_timestamp_ns": ts + 1_500_000_000, "symbol": "NIFTY29OCT26105PE",
        "volume": 100, "oi": 1000,
    })
    results = scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 130.0, "ask": 131.0, "lot_size": 1,
        "source_timestamp_ns": ts + 1_500_000_000, "symbol": "NIFTY29OCT26FUT",
        "volume": 100, "oi": 1000,
    })
    assert results, "a real executable pair within the bounded timestamp skew should be evaluated"
    assert scanner.diagnostics_snapshot()["counters"].get("timestamp_mismatch", 0) == 0
    assert all(result.option.timestamp_ns == result.future.timestamp_ns for result in results)


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
            "bid": 117.0,
            "ask": 118.0,
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
            "bid": 117.0,
            "ask": 118.0,
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
    scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "bid_qty": 0, "ask_qty": 0})
    result = scanner.observe(
        {
            "underlying": "NIFTY",
            "instrument_class": "INDEX",
            "option_type": "",
            "expiry": "30SEP2026",
            "bid": 117.0,
            "ask": 118.0,
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
    for exp, fut in (("30SEP2026", 117.0), ("07OCT2026", 118.0)):
        scanner.observe({**_base(ts, 100.0, "CE", 4.0, 5.0), "expiry": exp})
        scanner.observe({**_base(ts, 100.0, "PE", 4.0, 5.0), "expiry": exp})
        scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": exp})
        scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": exp})
        result = scanner.observe({"underlying":"NIFTY","instrument_class":"INDEX","option_type":"",
            "expiry":exp,"bid":fut,"ask":fut+1,"lot_size":1,"source_timestamp_ns":ts,
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
    scanner.observe(_base(ts + 2_000_000_001, 105.0, "PE", 4.0, 5.0))
    result = scanner.observe({"underlying":"NIFTY","instrument_class":"INDEX","option_type":"",
        "expiry":"30SEP2026","bid":115,"ask":116,"lot_size":1,"source_timestamp_ns":ts+2_000_000_000,"symbol":"NIFTYFUT"})
    assert result == ()


def test_live_synthetic_scanner_allows_synchronized_ticks_across_second_boundary():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    ts = 5_999_999_999
    scanner.observe(_base(ts, 100.0, "CE", 4.0, 5.0))
    scanner.observe(_base(ts, 100.0, "PE", 4.0, 5.0))
    scanner.observe(_base(ts, 105.0, "CE", 4.0, 5.0))
    scanner.observe(_base(ts + 2, 105.0, "PE", 4.0, 5.0))
    result = scanner.observe({"underlying":"NIFTY","instrument_class":"INDEX","option_type":"", "expiry":"30SEP2026", "bid":117,"ask":118,"lot_size":1,"source_timestamp_ns":ts+2,"symbol":"NIFTYFUT","volume":100,"oi":1000})
    assert result
    assert result[0].option.timestamp_ns == ts + 2
    assert result[0].future.timestamp_ns == ts + 2


def test_live_synthetic_scanner_rejects_commodity_scope():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    payload = _base(6_000_000_000, 105.0, "CE", 4.0, 5.0)
    payload["instrument_class"] = "COMMODITY"
    assert scanner.observe(payload) == ()



def test_live_synthetic_scanner_rejects_future_epoch_timestamp_and_counts_reason():
    import time

    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    payload = _base(time.time_ns() + 60_000_000_000, 100.0, "CE", 4.0, 5.0)
    assert scanner.observe(payload) == ()
    assert scanner.diagnostics_snapshot()["counters"]["future_timestamp"] == 1


def test_live_synthetic_scanner_rejects_stale_epoch_timestamp_and_counts_reason():
    import time

    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    payload = _base(time.time_ns() - 6_000_000_000, 100.0, "CE", 4.0, 5.0)
    assert scanner.observe(payload) == ()
    counters = scanner.diagnostics_snapshot()["counters"]
    assert counters["stale_timestamp"] == 1
    assert counters["last_payload_received_at_ns"] > 0
    assert counters["last_observation_at_ns"] is None
    assert counters["source_latency_samples"] == 1
    assert counters["source_latency_over_5s"] == 1

def test_live_synthetic_scanner_rejects_malformed_timestamp_without_raising():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    payload = _base("not-a-timestamp", 100.0, "CE", 4.0, 5.0)
    assert scanner.observe(payload) == ()
    assert scanner.diagnostics_snapshot()["counters"]["invalid_timestamp"] == 1


def test_live_synthetic_scanner_diagnostics_count_missing_future_leg():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner.observe(_base(7_000_000_000, 100.0, "CE", 4.0, 5.0))
    assert scanner.diagnostics_snapshot()["counters"]["missing_future_leg"] == 1


def test_live_synthetic_scanner_rejects_non_mapping_payload():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    assert scanner.observe(None) == ()
    assert scanner.diagnostics_snapshot()["counters"]["invalid_payload_or_scope"] == 1


def test_live_synthetic_scanner_waits_for_all_three_legs_in_any_arrival_order():
    ts = 8_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    future = {
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": "29OCT2026", "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts + 10, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    }
    ce = {**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": "29OCT2026"}
    pe = {**_base(ts + 5, 105.0, "PE", 4.0, 5.0), "expiry": "29OCT2026"}

    # Future can arrive first; it remains pending until CE and PE complete the triplet.
    assert scanner.observe(future) == ()
    assert scanner.observe(ce) == ()
    result = scanner.observe(pe)
    assert result
    assert result[0].option.strike == 105.0
    assert result[0].option.timestamp_ns == ts + 10
    assert result[0].future.timestamp_ns == ts + 10


def test_live_synthetic_scanner_skips_timestamp_mismatched_strike_without_blocking_valid_strike():
    ts = 9_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 95.0)
    # The 100 strike is synchronized and complete; the 105 strike has an old CE.
    scanner.observe({**_base(ts, 100.0, "CE", 4.0, 5.0), "expiry": "29OCT2026"})
    scanner.observe({**_base(ts, 100.0, "PE", 4.0, 5.0), "expiry": "29OCT2026"})
    scanner.observe({**_base(ts - 2_100_000_000, 105.0, "CE", 4.0, 5.0), "expiry": "29OCT2026"})
    scanner.observe({**_base(ts + 900_000_000, 105.0, "PE", 4.0, 5.0), "expiry": "29OCT2026"})
    result = scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": "29OCT2026", "bid": 115.0, "ask": 116.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    })
    # A bad pair is skipped independently; it cannot invalidate the good 100 strike.
    assert result
    assert all(r.option.strike == 100.0 for r in result)
    assert scanner.diagnostics_snapshot()["counters"]["missing_call_leg"] >= 1



def test_live_scanner_accepts_executable_depth_when_volume_and_oi_are_missing(monkeypatch):
    import app.scanner.live_synthetic_scanner as scanner_module

    monkeypatch.setattr(scanner_module, "scan_synthetic_snapshot", lambda *_a, **_k: ())
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    ts = 10_500_000_000
    expiry = "29OCT2026"
    ce = {**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": expiry}
    pe = {**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": expiry}
    ce.pop("volume")
    ce.pop("oi")
    pe.pop("volume")
    pe.pop("oi")
    future = {
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT",
    }

    scanner.observe(ce)
    scanner.observe(pe)
    scanner.observe(future)

    counters = scanner.diagnostics_snapshot()["counters"]
    assert counters.get("illiquid_or_incomplete_option_leg", 0) == 0
    assert counters.get("scan_evaluations", 0) == 1
    assert counters.get("scan_evaluations_by_symbol:NIFTY", 0) == 1
    assert counters.get("successful_pairs_by_symbol:NIFTY", 0) == 1
    assert counters.get("empty_calculations_by_symbol:NIFTY", 0) == 1
    assert counters.get("opportunities_emitted_by_symbol:NIFTY", 0) == 0
    assert counters.get("latest_scan_result_count_by_symbol:NIFTY", 0) == 0
    assert counters.get("latest_scan_at_ns_by_symbol:NIFTY", 0) > 0
    assert counters.get("latest_scan_status_by_symbol:NIFTY") == "NO_EDGE"


def test_ltp_only_update_does_not_erase_executable_book_or_refresh_its_timestamp():
    ts = 10_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    ce = {**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": "29OCT2026"}
    newer_ltp_only = {
        **ce, "source_timestamp_ns": ts + 500_000_000,
        "bid": None, "ask": None, "bid_qty": None, "ask_qty": None, "ltp": 4.5,
    }

    scanner.observe(ce)
    scanner.observe(newer_ltp_only)

    bucket = next(iter(scanner._buckets.values()))
    cached = bucket["options"][105.0]["CE"]
    assert cached["bid"] == 4.0
    assert cached["ask"] == 5.0
    assert cached["bid_qty"] == ce["bid_qty"]
    assert cached["ask_qty"] == ce["ask_qty"]
    assert cached["_quote_source_timestamp_ns"] == ts
    assert cached["source_timestamp_ns"] == ts + 500_000_000


def test_live_synthetic_scanner_waits_up_to_three_seconds_for_missing_leg_quote():
    ts = 10_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    future = {
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": "29OCT2026", "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    }
    ce_ltp = {**_base(ts, 105.0, "CE", None, None), "expiry": "29OCT2026", "ltp": 1.0}
    pe_ltp = {**_base(ts, 105.0, "PE", None, None), "expiry": "29OCT2026", "ltp": 1.0}

    assert scanner.observe(future) == ()
    assert scanner.observe(ce_ltp) == ()
    assert scanner.observe(pe_ltp) == ()
    assert scanner.diagnostics_snapshot()["counters"]["waiting_for_executable_quote"] >= 1

    # The executable book may arrive later; it must complete the original LTP
    # candidate within three seconds without being treated as a new unmatched leg.
    ce_quote = {**ce_ltp, "bid": 1.0, "ask": 2.0, "source_timestamp_ns": ts + 2_500_000_000}
    pe_quote = {**pe_ltp, "bid": 1.0, "ask": 2.0, "source_timestamp_ns": ts + 2_500_000_000}
    assert scanner.observe(ce_quote) == ()
    assert scanner.observe(pe_quote) == ()

    # The pairing anchor must not disguise the old future timestamp. The monitor
    # reports the actual quote-time mismatch until a synchronized future tick arrives.
    row = next(r for r in scanner.pair_monitor_snapshot(limit=10)["data"] if r["strike"] == 105.0)
    assert row["state"] == "INCOMPLETE"
    assert "TIMESTAMP_MISMATCH" in row["reason"]
    assert row["timestamp_ns"] == ts + 2_500_000_000

    result = scanner.observe({**future, "source_timestamp_ns": ts + 2_500_000_000})
    assert result
    assert result[0].option.strike == 105.0


def test_pair_monitor_exposes_incomplete_combo_without_raw_payload():
    ts = 11_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": "29OCT2026"})
    rows = scanner.pair_monitor_snapshot(limit=10)["data"]
    assert len(rows) == 1
    assert rows[0]["underlying"] == "NIFTY"
    assert rows[0]["strike"] == 105.0
    assert rows[0]["state"] == "INCOMPLETE"
    assert rows[0]["reason"] == "MISSING_FUTURE,MISSING_PUT"
    assert "raw_broker_payload" not in rows[0]


def test_pair_monitor_marks_complete_synchronized_triplet_ready():
    ts = 12_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    expiry = "29OCT2026"
    scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({**_base(ts + 100, 105.0, "PE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts + 200, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    })
    rows = scanner.pair_monitor_snapshot(limit=10)["data"]
    row = next(row for row in rows if row["strike"] == 105.0)
    assert row["state"] == "READY_TO_EVALUATE"
    assert row["reason"] == "AWAITING_SCANNER_QUALIFICATION"
    assert row["readiness_scope"] == "STRUCTURAL_ONLY"
    assert row["opportunity_confirmed"] is False
    assert row["qualification_status"] == "NOT_CONFIRMED"
    assert row["future_bid"] == 117.0
    assert row["call_bid"] == 4.0
    assert row["put_bid"] == 4.0


def test_pair_monitor_survives_malformed_cached_leg_timestamp():
    ts = 13_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": "29OCT2026"})
    bucket = scanner._buckets[("NIFTY", ts, 20261029)]
    bucket["options"][105.0]["CE"]["source_timestamp_ns"] = "not-a-timestamp"

    snapshot = scanner.pair_monitor_snapshot(limit=10)
    assert snapshot["status"] == "success"
    assert len(snapshot["data"]) == 1
    assert "INVALID_TIMESTAMP" in snapshot["data"][0]["reason"]



def test_pair_monitor_rejects_nonfinite_depth_instead_of_ready():
    for invalid_depth in (float("inf"), float("nan"), True):
        ts = 14_000_000_000
        scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
        expiry = "29OCT2026"
        scanner.observe({
            **_base(ts, 105.0, "CE", 4.0, 5.0),
            "expiry": expiry, "bid_qty": invalid_depth,
        })
        scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": expiry})
        scanner.observe({
            "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
            "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
            "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
        })
        row = next(r for r in scanner.pair_monitor_snapshot(limit=10)["data"] if r["strike"] == 105.0)
        assert row["state"] == "INCOMPLETE"
        assert "CALL_DEPTH_MISSING" in row["reason"]


def test_pair_monitor_reports_missing_executable_depth_instead_of_ready():
    ts = 14_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    expiry = "29OCT2026"
    scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": expiry, "bid_qty": 0})
    scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    })
    row = next(r for r in scanner.pair_monitor_snapshot(limit=10)["data"] if r["strike"] == 105.0)
    assert row["state"] == "INCOMPLETE"
    assert "CALL_DEPTH_MISSING" in row["reason"]


def test_pair_monitor_reports_lot_size_mismatch_instead_of_ready():
    ts = 15_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    expiry = "29OCT2026"
    scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": expiry, "lot_size": 50})
    scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": expiry, "lot_size": 25})
    scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 50,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    })
    row = next(r for r in scanner.pair_monitor_snapshot(limit=10)["data"] if r["strike"] == 105.0)
    assert row["state"] == "INCOMPLETE"
    assert "LOT_SIZE_MISMATCH" in row["reason"]


def test_pair_monitor_reports_missing_atm_instead_of_ready():
    ts = 16_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: None)
    expiry = "29OCT2026"
    scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    })
    counters = scanner.diagnostics_snapshot()["counters"]
    assert counters["missing_atm_price"] == 1
    assert counters["missing_atm_price_by_symbol:NIFTY"] == 1
    row = next(r for r in scanner.pair_monitor_snapshot(limit=10)["data"] if r["strike"] == 105.0)
    assert row["state"] == "INCOMPLETE"
    assert "MISSING_ATM" in row["reason"]



def test_pair_monitor_exposes_future_expiry_mismatch_instead_of_hiding_pair():
    ts = 17_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    expiry = "29OCT2026"
    scanner.observe({**_base(ts, 105.0, "CE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({**_base(ts, 105.0, "PE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    })
    bucket = scanner._buckets[("NIFTY", ts, 20261029)]
    bucket["future"]["expiry"] = "26NOV2026"

    rows = scanner.pair_monitor_snapshot(limit=10)["data"]
    row = next(r for r in rows if r["strike"] == 105.0)
    assert row["state"] == "INCOMPLETE"
    assert row["reason"].count("EXPIRY_MISMATCH") == 1



def test_live_scanner_rejects_boolean_contract_lot_sizes():
    for bad_leg in ("call", "put", "future"):
        ts = 1_100_000_000
        scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
        expiry = "30SEP2026"
        ce = {**_base(ts, 100.0, "CE", 4.0, 5.0), "expiry": expiry}
        pe = {**_base(ts, 100.0, "PE", 4.0, 5.0), "expiry": expiry}
        future = {
            "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
            "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
            "source_timestamp_ns": ts, "symbol": "NIFTYFUT",
        }
        if bad_leg == "call":
            ce["lot_size"] = True
        elif bad_leg == "put":
            pe["lot_size"] = True
        else:
            future["lot_size"] = True

        scanner.observe(ce)
        scanner.observe(pe)
        scanner.observe(future)
        counters = scanner.diagnostics_snapshot()["counters"]
        assert counters.get("scan_evaluations", 0) == 0
        assert counters.get("invalid_option_lot_size", 0) + counters.get("invalid_future_lot_size", 0) >= 1
        row = next(r for r in scanner.pair_monitor_snapshot(limit=10)["data"] if r["strike"] == 100.0)
        assert row["state"] == "INCOMPLETE"
        assert "INVALID_LOT_SIZE" in row["reason"]


def test_live_scanner_uses_future_timestamp_for_atm_lookup(monkeypatch):
    import app.scanner.live_synthetic_scanner as scanner_module

    monkeypatch.setattr(scanner_module, "scan_synthetic_snapshot", lambda *_a, **_k: ())
    ts = 1_200_000_000
    atm_timestamps = []
    scanner = LiveSyntheticScanner(
        atm_provider=lambda _s, timestamp: atm_timestamps.append(timestamp) or 100.0
    )
    expiry = "29OCT2026"
    future = {
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT",
    }
    scanner.observe(future)
    scanner.observe({**_base(ts + 500_000_000, 105.0, "CE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({**_base(ts + 500_000_000, 105.0, "PE", 4.0, 5.0), "expiry": expiry})

    assert atm_timestamps
    assert set(atm_timestamps) == {ts}


def test_live_scanner_rejects_mismatched_ce_pe_future_lot_sizes():
    ts = 1_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    expiry = "30SEP2026"
    scanner.observe({**_base(ts, 100.0, "CE", 4.0, 5.0), "expiry": expiry, "lot_size": 50})
    scanner.observe({**_base(ts, 100.0, "PE", 4.0, 5.0), "expiry": expiry, "lot_size": 25})
    scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 50,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,    })

    counters = scanner.diagnostics_snapshot()["counters"]
    assert counters["lot_size_mismatch"] == 1
    assert counters.get("scan_evaluations", 0) == 0



def test_live_scanner_rejects_non_finite_executable_quotes():
    ts = 1_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    expiry = "30SEP2026"
    scanner.observe({**_base(ts, 100.0, "CE", float("inf"), 5.0), "expiry": expiry})
    scanner.observe({**_base(ts, 100.0, "PE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": float("nan"), "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    })
    counters = scanner.diagnostics_snapshot()["counters"]
    assert counters.get("scan_evaluations", 0) == 0
    assert counters.get("invalid_quote", 0) >= 1


def test_live_scanner_handles_non_finite_volume_and_oi_without_crashing():
    ts = 1_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    expiry = "30SEP2026"
    scanner.observe({
        **_base(ts, 100.0, "CE", 4.0, 5.0),
        "expiry": expiry, "volume": float("nan"), "oi": 1000,
    })
    scanner.observe({
        **_base(ts, 100.0, "PE", 4.0, 5.0),
        "expiry": expiry, "volume": 100, "oi": 1000,
    })
    result = scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT",
        "volume": float("inf"), "oi": float("nan"),
    })
    assert isinstance(result, tuple)
    assert scanner.diagnostics_snapshot()["counters"].get("scan_evaluations", 0) <= 1



def test_live_scanner_contains_atm_provider_failure():
    ts = 1_000_000_000
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: (_ for _ in ()).throw(RuntimeError("ATM unavailable")))
    expiry = "30SEP2026"
    scanner.observe({**_base(ts, 100.0, "CE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({**_base(ts, 100.0, "PE", 4.0, 5.0), "expiry": expiry})
    result = scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    })
    assert result == ()
    counters = scanner.diagnostics_snapshot()["counters"]
    assert counters["atm_provider_errors"] == 1
    assert counters["atm_provider_errors_by_symbol:NIFTY"] == 1


def test_live_scanner_contains_config_provider_failure():
    ts = 1_000_000_000
    scanner = LiveSyntheticScanner(
        atm_provider=lambda _s, _t: 100.0,
        config_provider=lambda _s: (_ for _ in ()).throw(RuntimeError("config unavailable")),
    )
    expiry = "30SEP2026"
    scanner.observe({**_base(ts, 100.0, "CE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({**_base(ts, 100.0, "PE", 4.0, 5.0), "expiry": expiry})
    result = scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    })
    assert result == ()
    counters = scanner.diagnostics_snapshot()["counters"]
    assert counters["config_provider_errors"] == 1
    assert counters["config_provider_errors_by_symbol:NIFTY"] == 1


def test_live_scanner_contains_result_callback_failure(monkeypatch):
    import app.scanner.live_synthetic_scanner as scanner_module
    monkeypatch.setattr(scanner_module, "scan_synthetic_snapshot", lambda *_a, **_k: ("safe-result",))
    scanner = LiveSyntheticScanner(
        atm_provider=lambda _s, _t: 100.0,
        on_result=lambda _results: (_ for _ in ()).throw(RuntimeError("callback unavailable")),
    )
    ts = 1_000_000_000
    expiry = "30SEP2026"
    scanner.observe({**_base(ts, 100.0, "CE", 4.0, 5.0), "expiry": expiry})
    scanner.observe({**_base(ts, 100.0, "PE", 4.0, 5.0), "expiry": expiry})
    result = scanner.observe({
        "underlying": "NIFTY", "instrument_class": "INDEX", "option_type": "",
        "expiry": expiry, "bid": 117.0, "ask": 118.0, "lot_size": 1,
        "source_timestamp_ns": ts, "symbol": "NIFTYFUT", "volume": 100, "oi": 1000,
    })
    assert result == ("safe-result",)
    assert scanner.diagnostics_snapshot()["counters"]["result_callback_errors"] == 1



def test_bucket_matching_does_not_merge_unrelated_snapshots_but_keeps_pending_quote_wait():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    ts = 20_000_000_000
    expiry = 20261029
    scanner._buckets[("NIFTY", ts, expiry)] = {
        "future": None,
        "options": {},
        "first_seen_monotonic_ns": __import__("time").monotonic_ns(),
    }

    # A normal snapshot 2.5s later must not be merged into a completed/non-pending
    # bucket merely because it falls inside the wider delayed-quote horizon.
    assert scanner._matching_bucket_key("NIFTY", ts + 2_500_000_000, expiry) == (
        "NIFTY", ts + 2_500_000_000, expiry
    )

    # The same wider horizon remains available when executable quotes are pending.
    scanner._buckets[("NIFTY", ts, expiry)]["quote_wait_started_ns"] = __import__("time").monotonic_ns()
    assert scanner._matching_bucket_key("NIFTY", ts + 2_500_000_000, expiry) == (
        "NIFTY", ts, expiry
    )


def test_synthetic_bucket_pruning_does_not_move_backwards_on_delayed_tick():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner._buckets = {
        ("NIFTY", 10_000_000_000, 20260930): {},
        ("NIFTY", 11_000_000_000, 20260930): {},
        ("NIFTY", 15_000_000_000, 20260930): {},
    }

    scanner._prune(10_000_000_000)

    assert ("NIFTY", 10_000_000_000, 20260930) not in scanner._buckets
    assert ("NIFTY", 11_000_000_000, 20260930) not in scanner._buckets
    assert ("NIFTY", 15_000_000_000, 20260930) in scanner._buckets
    assert scanner.diagnostics_snapshot()["counters"]["pruned_buckets"] == 2


def test_synthetic_bucket_cache_has_hard_capacity_limit():
    scanner = LiveSyntheticScanner(atm_provider=lambda _s, _t: 100.0)
    scanner._buckets = {
        (f"SYM{i}", i, 20260930): {} for i in range(scanner.MAX_ACTIVE_BUCKETS + 25)
    }

    scanner._prune(scanner.MAX_ACTIVE_BUCKETS + 24)

    assert len(scanner._buckets) <= scanner.MAX_ACTIVE_BUCKETS
    assert scanner.diagnostics_snapshot()["counters"]["bucket_capacity_evictions"] == 25



def test_pair_monitor_survives_atm_provider_exception_and_marks_missing_atm():
    def broken_atm(_symbol, _timestamp):
        raise RuntimeError("provider unavailable")

    scanner = LiveSyntheticScanner(atm_provider=broken_atm)
    ts = 1_000_000_000
    expiry = "30SEP2026"
    scanner._buckets[("NIFTY", ts, 20260930)] = {
        "future": {
            "expiry": expiry, "source_timestamp_ns": ts, "bid": 117, "ask": 118,
            "lot_size": 1, "volume": 100, "oi": 1000,
        },
        "options": {
            105.0: {
                "CE": {**_base(ts, 105.0, "CE", 4.0, 5.0), "bid_qty": 10, "ask_qty": 10, "volume": 100, "lot_size": 1},
                "PE": {**_base(ts, 105.0, "PE", 4.0, 5.0), "bid_qty": 10, "ask_qty": 10, "volume": 100, "lot_size": 1},
            }
        },
    }

    snapshot = scanner.pair_monitor_snapshot(limit=10)
    row = next(r for r in snapshot["data"] if r["strike"] == 105.0)
