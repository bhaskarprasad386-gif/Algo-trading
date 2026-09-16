import pytest

from app.backtesting.execution import ExecutionSide, SimFill, SimOrder
from app.backtesting.portfolio import Portfolio, RiskConfig, RiskViolation
from app.backtesting.risk_controls import enforce_market_risk, evaluate_market_risk, order_reduces_position_risk


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


def test_enforce_market_risk_blocks_new_risk_on_maintenance_margin_call():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 300, 500))
    before = p.export_state()
    state = evaluate_market_risk(p, {"X": 100})
    assert state.margin_call is True
    with pytest.raises(RiskViolation, match="maintenance margin"):
        enforce_market_risk(p, {"X": 100})
    assert p.export_state() == before


def test_market_risk_uses_current_marks_for_leverage_and_notional():
    p = Portfolio(10_000, RiskConfig(initial_margin_rate=0.1, max_leverage=2.0, max_gross_notional=25_000))
    p.apply_fill(fill("b", "X", ExecutionSide.BUY, 100, 100))
    state = evaluate_market_risk(p, {"X": 250})
    assert state.gross_notional == 25_000
    assert state.leverage == pytest.approx(1.0)
    with pytest.raises(RiskViolation, match="max gross notional"):
        enforce_market_risk(p, {"X": 250.01})


def test_risk_reducing_order_is_allowed_to_unwind_long_position():
    p = Portfolio(100_000)
    p.apply_fill(fill("open", "X", ExecutionSide.BUY, 10, 100))
    assert order_reduces_position_risk(p, SimOrder("close", "X", ExecutionSide.SELL, 5)) is True
    assert order_reduces_position_risk(p, SimOrder("add", "X", ExecutionSide.BUY, 5)) is False


def test_risk_reducing_classification_does_not_require_market_marks():
    p = Portfolio(100_000)
    p.apply_fill(fill("open", "X", ExecutionSide.BUY, 10, 100))
    with pytest.raises(ValueError, match="missing market mark"):
        p.snapshot()
    assert order_reduces_position_risk(p, SimOrder("close", "X", ExecutionSide.SELL, 10)) is True


def test_atomic_risk_reducing_close_is_allowed_when_marked_equity_is_negative():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    p.apply_fill(fill("open", "X", ExecutionSide.BUY, 300, 500))
    snapshot = p.apply_fills_atomic((fill("close", "X", ExecutionSide.SELL, 300, 100),), {"X": 100.0})
    assert snapshot.positions == ()
    assert snapshot.cash == pytest.approx(-20_000.0)


def test_risk_reducing_order_is_allowed_to_unwind_short_position():
    p = Portfolio(100_000)
    p.apply_fill(fill("open", "X", ExecutionSide.SELL, 10, 100))
    assert order_reduces_position_risk(p, SimOrder("close", "X", ExecutionSide.BUY, 5)) is True
    assert order_reduces_position_risk(p, SimOrder("add", "X", ExecutionSide.SELL, 5)) is False


def test_order_that_reverses_position_is_not_classified_as_reducing():
    p = Portfolio(100_000)
    p.apply_fill(fill("open", "X", ExecutionSide.BUY, 10, 100))
    assert order_reduces_position_risk(p, SimOrder("reverse", "X", ExecutionSide.SELL, 11)) is False
    assert order_reduces_position_risk(p, SimOrder("flat", "X", ExecutionSide.SELL, 10)) is True
