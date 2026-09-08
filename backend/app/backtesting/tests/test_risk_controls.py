import pytest

from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.portfolio import Portfolio, RiskConfig, RiskViolation
from app.backtesting.risk_controls import enforce_market_risk, evaluate_market_risk


def fill(order_id, instrument, side, quantity, price, fee=0.0):
    return SimFill(order_id, instrument, side, quantity, price, 1, fee)


def test_mark_to_market_risk_detects_drawdown_without_mutating_portfolio():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, max_drawdown=500))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 10, 100))
    before = p.export_state()
    state = evaluate_market_risk(p, {"X": 40})
    assert state.equity == 99_400
    assert state.drawdown == 600
    assert state.max_drawdown_breached is True
    assert p.export_state() == before


def test_enforce_market_risk_fails_closed_on_mark_to_market_drawdown():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, max_drawdown=500))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 10, 100))
    with pytest.raises(RiskViolation, match="max drawdown"):
        enforce_market_risk(p, {"X": 40})


def test_market_risk_detects_maintenance_margin_call():
    p = Portfolio(10_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.25))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 100, 100))
    state = evaluate_market_risk(p, {"X": 30})
    assert state.equity == 3_000
    assert state.maintenance_margin == 750
    assert state.margin_call is False


def test_market_risk_uses_current_marks_for_leverage_and_notional():
    p = Portfolio(10_000, RiskConfig(initial_margin_rate=0.1, max_leverage=2.0, max_gross_notional=25_000))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 100, 100))
    state = evaluate_market_risk(p, {"X": 250})
    assert state.gross_notional == 25_000
    assert state.leverage == pytest.approx(2.5)
    with pytest.raises(RiskViolation, match="max gross notional"):
        enforce_market_risk(p, {"X": 250.01})
