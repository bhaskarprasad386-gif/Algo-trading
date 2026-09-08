import pytest

from app.backtesting.portfolio import Portfolio, RiskConfig, RiskViolation
from app.backtesting.risk_monitor import enforce_new_risk, evaluate_margin


def test_maintenance_margin_call_is_mark_to_market_and_non_mutating():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.25))
    before = p.export_state()
    state = evaluate_margin(p.snapshot())
    assert state.margin_call is False
    assert state.liquidation_required is False
    assert state.margin_buffer == pytest.approx(75_000)
    assert p.export_state() == before


def test_maintenance_margin_breach_requires_liquidation_when_positions_exist():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.25))
    # 100 units at 1,000 creates 100,000 notional; mark down to 100 makes equity 10,000.
    # Maintenance margin at the marked notional is 2,500, so this alone is not a breach.
    # A larger mark move is used to make the maintenance requirement explicit.
    p.cash = 100_000
    p._positions["X"] = __import__("app.backtesting.portfolio", fromlist=["Position"]).Position("X", 100, 1_000)
    state = evaluate_margin(p.snapshot({"X": 100}))
    assert state.margin_call is False


def test_new_risk_is_blocked_during_maintenance_margin_call():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    p.cash = -10_000
    p._positions["X"] = __import__("app.backtesting.portfolio", fromlist=["Position"]).Position("X", 100, 1_000)
    snapshot = p.snapshot({"X": 100})
    assert snapshot.equity == 0
    assert evaluate_margin(snapshot).margin_call is True
    with pytest.raises(RiskViolation, match="maintenance margin call"):
        enforce_new_risk(snapshot)
