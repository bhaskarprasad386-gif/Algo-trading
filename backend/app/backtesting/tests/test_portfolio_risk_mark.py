import pytest

from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.portfolio import Portfolio, RiskConfig, RiskViolation


def fill(order_id, instrument, side, quantity, price, fee=0.0):
    return SimFill(order_id, instrument, side, quantity, price, 1, fee)


def test_mark_to_market_risk_detects_maintenance_margin_breach_without_mutation():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 300, 500))
    before = p.export_state()
    with pytest.raises(RiskViolation, match="maintenance margin"):
        p.validate_mark_to_market({"X": 100})
    assert p.export_state() == before


def test_mark_to_market_risk_checks_drawdown_and_exposure_at_current_marks():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.1, max_gross_notional=20_000, max_leverage=2.0, max_drawdown=1_000))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 100, 100))
    with pytest.raises(RiskViolation, match="max gross notional"):
        p.validate_mark_to_market({"X": 250})

    p2 = Portfolio(100_000, RiskConfig(initial_margin_rate=0.1, max_drawdown=1_000))
    p2.apply_fill(fill("b", "X", ExecutionSide.BUY, 100, 100))
    with pytest.raises(RiskViolation, match="max drawdown"):
        p2.validate_mark_to_market({"X": 80})


def test_mark_to_market_risk_passes_and_returns_snapshot():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.2, maintenance_margin_rate=0.1, max_gross_notional=50_000, max_leverage=3.0, max_drawdown=20_000))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 100, 100))
    snapshot = p.validate_mark_to_market({"X": 105})
    assert snapshot.equity == pytest.approx(100_500)
    assert snapshot.maintenance_margin == pytest.approx(1_050)
