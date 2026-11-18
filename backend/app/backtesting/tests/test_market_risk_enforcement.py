import pytest

from app.backtesting.execution import ExecutionSide, SimFill, SimOrder
from app.backtesting.portfolio import Portfolio, RiskConfig, RiskViolation
from app.backtesting.risk_controls import enforce_market_risk, evaluate_market_risk, order_reduces_position_risk


def fill(order_id, instrument, side, quantity, price):
    return SimFill(order_id, instrument, side, quantity, price, 1, 0.0)


def test_market_risk_enforcement_blocks_maintenance_margin_breach():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    portfolio.apply_fill(fill("seed", "X", ExecutionSide.BUY, 300, 500))
    with pytest.raises(RiskViolation, match="maintenance margin"):
        enforce_market_risk(portfolio, {"X": 100})


def test_market_risk_evaluation_is_reporting_only_and_non_mutating():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    portfolio.apply_fill(fill("seed", "X", ExecutionSide.BUY, 300, 500))
    before = portfolio.export_state()
    state = evaluate_market_risk(portfolio, {"X": 100})
    assert state.margin_call is True
    assert portfolio.export_state() == before


def test_risk_reducing_order_is_only_position_reduction_not_reversal():
    portfolio = Portfolio(100_000)
    portfolio.apply_fill(fill("seed", "X", ExecutionSide.BUY, 10, 100))
    assert order_reduces_position_risk(portfolio, SimOrder("close", "X", ExecutionSide.SELL, 4)) is True
    assert order_reduces_position_risk(portfolio, SimOrder("flat", "X", ExecutionSide.SELL, 10)) is True
    assert order_reduces_position_risk(portfolio, SimOrder("reverse", "X", ExecutionSide.SELL, 11)) is False
    assert order_reduces_position_risk(portfolio, SimOrder("add", "X", ExecutionSide.BUY, 1)) is False


def test_risk_reducing_order_for_missing_position_is_not_allowed():
    portfolio = Portfolio(100_000)
    assert order_reduces_position_risk(portfolio, SimOrder("new", "X", ExecutionSide.SELL, 1)) is False
