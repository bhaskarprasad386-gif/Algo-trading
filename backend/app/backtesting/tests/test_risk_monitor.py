import pytest

from app.backtesting.portfolio import Portfolio, Position, RiskConfig, RiskViolation
from app.backtesting.risk_monitor import enforce_new_risk, evaluate_margin


def test_maintenance_margin_state_is_mark_to_market_and_non_mutating():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.25))
    before = p.export_state()
    state = evaluate_margin(p.snapshot())
    assert state.margin_call is False
    assert state.liquidation_required is False
    assert state.margin_buffer == pytest.approx(100_000)
    assert p.export_state() == before


def test_maintenance_margin_breach_requires_liquidation_when_positions_exist():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    p.cash = -60_000
    p._positions["X"] = Position("X", 100, 1_000)
    state = evaluate_margin(p.snapshot({"X": 100}))
    assert state.equity == pytest.approx(-50_000)
    assert state.margin_buffer == pytest.approx(-50_050)
    assert state.margin_call is True
    assert state.liquidation_required is True


def test_new_risk_is_blocked_during_maintenance_margin_call():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    p.cash = -60_000
    p._positions["X"] = Position("X", 100, 1_000)
    snapshot = p.snapshot({"X": 100})
    with pytest.raises(RiskViolation, match="maintenance margin call"):
        enforce_new_risk(snapshot)
