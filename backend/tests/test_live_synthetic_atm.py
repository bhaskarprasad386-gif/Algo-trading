from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker, concrete_strikes_from_master


def test_atm_tracker_uses_nearest_real_chain_strike():
    tracker = LiveSyntheticAtmTracker(
        strikes_by_symbol={"NIFTY": (100.0, 105.0, 110.0)}
    )
    assert tracker.atm("NIFTY") is None
    assert tracker.update("NIFTY", 107.0) == 105.0
    assert tracker.atm("NIFTY") == 105.0
    assert tracker.update("NIFTY", 108.0) == 110.0


def test_atm_tracker_allows_underlying_tick_within_scanner_timestamp_skew():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(
        strikes_by_symbol={"NIFTY": (100.0, 105.0, 110.0)}
    )
    source_timestamp = time_ns()
    tracker.update("NIFTY", 107.0, source_timestamp)

    # Derivative exchange timestamps may trail the live spot timestamp,
    # but ATM evaluation must remain inside the 5s freshness/skew bound.
    assert tracker.atm("NIFTY", source_timestamp - 4_900_000_000) == 105.0
    assert tracker.atm("NIFTY", source_timestamp - 5_100_000_000) is None


def test_atm_tracker_rejects_unknown_or_invalid_inputs():
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    assert tracker.update("BANKNIFTY", 100.0) is None
    try:
        tracker.update("NIFTY", 0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected positive price validation")

def test_concrete_strikes_from_master_uses_real_chain_and_expiry():
    instruments = [
        {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "30SEP2026", "strike": "9500"},
        {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "30SEP2026", "strike": "10000"},
        {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "30OCT2026", "strike": "10500"},
        {"name": "ABC", "exch_seg": "NFO", "instrumenttype": "OPTSTK", "expiry": "30SEP2026", "strike": "20000"},
        {"name": "NIFTY", "exch_seg": "NSE", "instrumenttype": "OPTIDX", "expiry": "30SEP2026", "strike": "11000"},
    ]
    assert concrete_strikes_from_master(instruments, symbols=("NIFTY",), expiry="30SEP2026") == {"NIFTY": (95.0, 100.0)}


def test_concrete_strikes_match_equivalent_expiry_formats_by_date():
    instruments = [
        {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "30SEP26", "strike": "2500000"},
        {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "2026-09-30", "strike": "2510000"},
        {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "30OCT2026", "strike": "2520000"},
    ]
    assert concrete_strikes_from_master(
        instruments, symbols=("NIFTY",), expiry="30SEP2026"
    ) == {"NIFTY": (25000.0, 25100.0)}


def test_atm_tracker_rejects_stale_underlying_price():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(
        strikes_by_symbol={"NIFTY": (100.0, 105.0, 110.0)}
    )
    old_timestamp = time_ns() - 6_000_000_000
    assert tracker.update("NIFTY", 107.0, old_timestamp) is None
    assert tracker.atm("NIFTY") is None


def test_atm_tracker_ignores_out_of_order_underlying_tick():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(
        strikes_by_symbol={"NIFTY": (100.0, 105.0, 110.0)}
    )
    latest = time_ns()
    assert tracker.update("NIFTY", 107.0, latest) == 105.0
    # An older tick must not overwrite the latest underlying price.
    assert tracker.update("NIFTY", 109.0, latest - 1_000_000_000) == 105.0
    assert tracker.atm("NIFTY") == 105.0


def test_atm_tracker_does_not_let_future_tick_poison_timestamp_ordering():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(
        strikes_by_symbol={"NIFTY": (100.0, 105.0, 110.0)}
    )
    future = time_ns() + 5_000_000_000
    assert tracker.update("NIFTY", 109.0, future) is None
    # A valid tick arriving after the rejected future-skewed tick is accepted.
    assert tracker.update("NIFTY", 102.0, time_ns()) == 100.0
    assert tracker.atm("NIFTY") == 100.0


def test_atm_tracker_diagnostics_separate_missing_chain_from_missing_price():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(
        strikes_by_symbol={"NIFTY": (100.0, 105.0), "ABC": (200.0, 205.0)}
    )
    tracker.update("NIFTY", 104.0, time_ns())
    snapshot = tracker.diagnostics_snapshot()
    by_symbol = {item["underlying"]: item for item in snapshot["symbols"]}

    assert snapshot["tracked_symbol_count"] == 2
    assert snapshot["atm_ready_symbol_count"] == 1
    assert by_symbol["NIFTY"]["concrete_strike_count"] == 2
    assert by_symbol["NIFTY"]["atm_ready"] is True
    assert by_symbol["ABC"]["concrete_strike_count"] == 2
    assert by_symbol["ABC"]["has_underlying_price"] is False
    assert by_symbol["ABC"]["atm_ready"] is False


def test_spot_snapshot_returns_real_fresh_underlying_price_with_age():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (22000.0, 22100.0)})
    now = time_ns()
    tracker.update("NIFTY", 22054.25, now - 250_000_000)

    snapshot = tracker.spot_snapshot("nifty", now_ns=now)

    assert snapshot is not None
    assert snapshot["spot_price"] == 22054.25
    assert snapshot["spot_timestamp_ns"] == now - 250_000_000
    assert snapshot["spot_age_seconds"] == 0.25


def test_spot_snapshot_fails_closed_for_missing_or_stale_underlying_price():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (22000.0, 22100.0)})
    now = time_ns()
    assert tracker.spot_snapshot("NIFTY", now_ns=now, max_age_seconds=10.0) is None

    tracker.update("NIFTY", 22054.25, now - 11_000_000_000)
    assert tracker.spot_snapshot("NIFTY", now_ns=now) is None


def test_spot_snapshot_rejects_unknown_symbols():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (22000.0, 22100.0)})
    tracker.update("NIFTY", 22054.25, time_ns())
    assert tracker.spot_snapshot("BANKNIFTY") is None



def test_spot_only_symbol_can_update_spot_without_concrete_strikes():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"BANKEX": ()})
    source_ns = time_ns()

    assert tracker.update("BANKEX", 61234.5, source_ns) is None
    assert tracker.atm("BANKEX", source_ns) is None
    snapshot = tracker.spot_snapshot("BANKEX", source_ns)
    assert snapshot == {
        "spot_price": 61234.5,
        "spot_timestamp_ns": source_ns,
        "spot_age_seconds": 0.0,
    }

    diagnostics = tracker.diagnostics_snapshot(source_ns)
    assert diagnostics["tracked_symbol_count"] == 1
    assert diagnostics["atm_ready_symbol_count"] == 0
    assert diagnostics["symbols_without_concrete_chain"] == ["BANKEX"]


def test_all_supported_index_symbols_can_publish_spot_without_option_chain():
    from time import time_ns
    from app.market_data.nifty50_universe import OPTION_STRATEGY_INDEX_SYMBOLS

    tracker = LiveSyntheticAtmTracker(
        strikes_by_symbol={symbol: () for symbol in OPTION_STRATEGY_INDEX_SYMBOLS}
    )
    source_ns = time_ns()

    for index, symbol in enumerate(sorted(OPTION_STRATEGY_INDEX_SYMBOLS), start=1):
        price = 10000.0 + index
        assert tracker.update(symbol, price, source_ns) is None
        snapshot = tracker.spot_snapshot(symbol, now_ns=source_ns)
        assert snapshot is not None
        assert snapshot["spot_price"] == price
        assert snapshot["spot_timestamp_ns"] == source_ns
        assert snapshot["spot_age_seconds"] == 0.0

    diagnostics = tracker.diagnostics_snapshot(source_ns)
    assert diagnostics["tracked_symbol_count"] == len(OPTION_STRATEGY_INDEX_SYMBOLS)
    assert diagnostics["atm_ready_symbol_count"] == 0
    assert set(diagnostics["symbols_without_concrete_chain"]) == OPTION_STRATEGY_INDEX_SYMBOLS



def test_atm_and_spot_accept_bounded_six_second_underlying_age():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(
        strikes_by_symbol={"NIFTY": (100.0, 105.0, 110.0)}
    )
    now = time_ns()
    tracker.update("NIFTY", 107.0, now - 6_000_000_000)

    assert tracker.atm("NIFTY", now, max_age_seconds=10.0) == 105.0
    snapshot = tracker.spot_snapshot("NIFTY", now_ns=now)
    assert snapshot is not None
    assert snapshot["spot_age_seconds"] == 6.0
