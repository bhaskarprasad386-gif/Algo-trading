import pytest

from app.backtesting.execution import ExecutionSide
from app.backtesting.liquidation import build_liquidation_orders
from app.backtesting.portfolio import Portfolio, Position, RiskConfig
from app.backtesting.risk_controls import order_reduces_position_risk


def test_liquidation_planner_is_empty_when_maintenance_margin_is_healthy():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio._positions["X"] = Position("X", 100, 100)

    before = portfolio.export_state()
    assert build_liquidation_orders(portfolio, {"X": 100}) == ()
    assert portfolio.export_state() == before


def test_liquidation_planner_flattens_long_and_short_positions_deterministically():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio.cash = -80_000
    portfolio._positions["B"] = Position("B", -50, 1_000)
    portfolio._positions["A"] = Position("A", 100, 1_000)

    before = portfolio.export_state()
    orders = build_liquidation_orders(portfolio, {"A": 100, "B": 100})

    assert [(o.order_id, o.instrument, o.side, o.quantity) for o in orders] == [
        ("liquidation:A", "A", ExecutionSide.SELL, 100),
        ("liquidation:B", "B", ExecutionSide.BUY, 50),
    ]
    assert all(order_reduces_position_risk(portfolio, order) for order in orders)
    assert portfolio.export_state() == before


def test_liquidation_planner_skips_unmarked_positions():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio.cash = -80_000
    portfolio._positions["A"] = Position("A", 100, 1_000)
    portfolio._positions["B"] = Position("B", 50, 1_000)

    orders = build_liquidation_orders(portfolio, {"A": 100})
    assert [o.instrument for o in orders] == ["A"]


def test_liquidation_planner_is_non_mutating_with_custom_prefix():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio.cash = -80_000
    portfolio._positions["X"] = Position("X", 100, 1_000)

    before = portfolio.export_state()
    orders = build_liquidation_orders(portfolio, {"X": 100}, order_id_prefix="forced")

    assert len(orders) == 1
    assert orders[0].order_id == "forced:X"
    assert portfolio.export_state() == before
