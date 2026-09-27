from app.market_data.synthetic_refresh import SyntheticAtmRefreshGate


def test_refresh_gate_requests_only_on_real_atm_change():
    gate = SyntheticAtmRefreshGate(100.0)
    assert gate.observe(100.0).refresh is False
    decision = gate.observe(105.0)
    assert decision.refresh is True
    assert decision.atm_strike == 105.0
    assert gate.observe(105.0).refresh is False


def test_refresh_gate_rejects_invalid_atm():
    gate = SyntheticAtmRefreshGate(100.0)
    try:
        gate.observe(0)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid ATM must be rejected")
