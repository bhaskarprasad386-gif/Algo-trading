import pytest

from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.portfolio import Portfolio, Position, RiskConfig, RiskViolation
from app.backtesting.portfolio_risk import evaluate_mark_to_market


def fill(order_id, instrument, side, quantity, price, fee=0.0):
    return SimFill(order_id, instrument, side, quantity, price, 1, fee)


def test_mark_to_market_reports_total_pnl_from_equity():
    p = Portfolio(100_000)
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 10, 100))
    risk = evaluate_mark_to_market(p, {"X": 115})
    assert risk.snapshot.equity == 100_150
    assert risk.snapshot.unrealized_pnl == 150
    assert risk.total_pnl == 150
    assert risk.maintenance_breach is False


def test_mark_to_market_detects_maintenance_margin_breach():
    p = Portfolio(10_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.25))
    p.cash = -3_000
    p._positions["X"] = Position("X", 10, 1_000)
    risk = evaluate_mark_to_market(p, {"X": 20})
    assert risk.maintenance_breach is True
    assert risk.snapshot.equity == -2_800
    assert risk.snapshot.maintenance_margin == 50


def test_mark_to_market_rejects_drawdown_before_state_change():
    p = Portfolio(100_000, RiskConfig(max_drawdown=100))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 10, 100))
    before = p.export_state()
    with pytest.raises(RiskViolation, match="mark-to-market"):
        evaluate_mark_to_market(p, {"X": 80})
    assert p.export_state() == before


def test_mark_to_market_rejects_invalid_marks():
    p = Portfolio()
    with pytest.raises(ValueError, match="finite and positive"):
        evaluate_mark_to_market(p, {"X": 0})
