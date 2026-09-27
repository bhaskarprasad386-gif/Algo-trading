from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker


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
