from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner
from app.scanner.box_spread import BoxSpreadScanConfig


def test_live_box_spread_diagnostics_explain_incomplete_call_put_pair():
    scanner = LiveBoxSpreadScanner(
        atm_provider=lambda _symbol, _timestamp: 100,
        config_provider=lambda _symbol: BoxSpreadScanConfig(),
    )
    payload = {
        "underlying": "NIFTY",
        "instrument_class": "INDEX",
        "source_timestamp_ns": 10,
        "expiry": "03NOV2026",
        "strike": 100,
        "option_type": "CE",
        "bid": 10,
        "ask": 10.5,
        "lot_size": 65,
        "volume": 100,
        "oi": 100,
    }

    assert scanner.observe(payload) == ()
    snapshot = scanner.diagnostics_snapshot()
    assert snapshot["counts"]["ticks_accepted_for_assembly"] == 1
    assert snapshot["counts"]["no_executable_grouped_strikes"] == 1
    assert snapshot["last_scan"]["outcome"] == "waiting_for_complete_executable_call_put_pairs"


def test_live_box_spread_diagnostics_count_stale_epoch_quote():
    scanner = LiveBoxSpreadScanner(atm_provider=lambda _symbol, _timestamp: 100)
    payload = {
        "underlying": "NIFTY",
        "instrument_class": "INDEX",
        "source_timestamp_ns": 1_000_000_000_000_000,
        "expiry": "03NOV2026",
        "strike": 100,
        "option_type": "CE",
        "bid": 10,
        "ask": 10.5,
    }

    assert scanner.observe(payload) == ()
    assert scanner.diagnostics_snapshot()["counts"]["stale_timestamp"] == 1
