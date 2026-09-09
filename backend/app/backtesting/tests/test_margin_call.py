from app.backtesting.execution import ExecutionSimulator, ExecutionSide, SimOrder
from app.backtesting.liquidation import execute_liquidation_orders
from app.backtesting.portfolio import Portfolio, Position, RiskConfig
from app.backtesting.risk_controls import (
    MarginCallState,
    evaluate_market_risk,
    order_reduces_position_risk,
    transition_margin_call_state,
)


def test_margin_call_lifecycle_enters_and_recovers_after_mtm_breach():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio._positions["X"] = Position("X", 100, 1_000)

    healthy = evaluate_market_risk(portfolio, {"X": 100})
    breached = evaluate_market_risk(portfolio, {"X": 400})

    assert transition_margin_call_state(MarginCallState.NORMAL, healthy) == MarginCallState.NORMAL
    assert breached.margin_call
    assert transition_margin_call_state(MarginCallState.NORMAL, breached) == MarginCallState.MARGIN_CALL
    assert transition_margin_call_state(MarginCallState.MARGIN_CALL, healthy) == MarginCallState.RECOVERED


def test_margin_call_keeps_risk_reducing_order_allowed_but_blocks_risk_increase():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio._positions["X"] = Position("X", 100, 1_000)

    reducing = SimOrder("forced:X", "X", ExecutionSide.SELL, 25)
    increasing = SimOrder("new:X", "X", ExecutionSide.BUY, 25)

    assert order_reduces_position_risk(portfolio, reducing)
    assert not order_reduces_position_risk(portfolio, increasing)


def test_liquidation_missing_mark_fails_without_portfolio_mutation():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio._positions["X"] = Position("X", 100, 1_000)
    order = SimOrder("forced:X", "X", ExecutionSide.SELL, 100)
    before = portfolio.export_state()

    result = execute_liquidation_orders(portfolio, (order,), ExecutionSimulator(), {}, 10)

    assert result[0].rejected
    assert result[0].remaining_quantity == 100
    assert "missing or invalid" in (result[0].reason or "")
    assert portfolio.export_state() == before


def test_liquidation_execution_failure_is_isolated_and_state_is_unchanged():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio._positions["X"] = Position("X", 100, 1_000)
    order = SimOrder("forced:X", "X", ExecutionSide.SELL, 100, submitted_at_ns=100)
    before = portfolio.export_state()

    result = execute_liquidation_orders(portfolio, (order,), ExecutionSimulator(), {"X": 1_000}, 10)

    assert result[0].rejected
    assert result[0].remaining_quantity == 100
    assert "execution failed" in (result[0].reason or "")
    assert portfolio.export_state() == before
