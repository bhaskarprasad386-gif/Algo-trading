from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker, concrete_strikes_from_master


def test_atm_tracker_uses_nearest_real_chain_strike():
    tracker = LiveSyntheticAtmTracker(
        strikes_by_symbol={"NIFTY": (100.0, 105.0, 110.0)}
    )
    assert tracker.atm("NIFTY") is None
    assert tracker.update("NIFTY", 107.0) == 105.0
    assert tracker.atm("NIFTY") == 105.0
    assert tracker.update("NIFTY", 108.0) == 110.0


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
