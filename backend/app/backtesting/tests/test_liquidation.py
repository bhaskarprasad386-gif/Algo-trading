import pytest

from app.backtesting.execution import DepthLevel, ExecutionConfig, ExecutionSide, ExecutionSimulator, OrderBook
from app.backtesting.liquidation import build_liquidation_orders, execute_liquidation_orders
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


def test_forced_liquidation_executes_long_and_short_positions():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio.cash = -80_000
    portfolio._positions["LONG"] = Position("LONG", 100, 1_000)
    portfolio._positions["SHORT"] = Position("SHORT", -50, 1_000)
    orders = build_liquidation_orders(portfolio, {"LONG": 100, "SHORT": 100})

    results = execute_liquidation_orders(
        portfolio, orders, ExecutionSimulator(), {"LONG": 100, "SHORT": 100}, 10
    )

    assert all(not result.rejected for result in results)
    assert portfolio.snapshot({"LONG": 100, "SHORT": 100}).positions == ()
    assert [(t.instrument, t.side, t.quantity) for t in portfolio.trades[-2:]] == [
        ("LONG", ExecutionSide.SELL, 100),
        ("SHORT", ExecutionSide.BUY, 50),
    ]


def test_forced_liquidation_partial_fill_does_not_duplicate_on_retry():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio.cash = -80_000
    portfolio._positions["X"] = Position("X", 100, 1_000)
    orders = build_liquidation_orders(portfolio, {"X": 100})
    book = OrderBook(bids=(DepthLevel(100, 40),))
    simulator = ExecutionSimulator(ExecutionConfig(allow_partial_fills=True))

    first = execute_liquidation_orders(portfolio, orders, simulator, {"X": 100}, 10, books={"X": book})
    assert first[0].remaining_quantity == 60
    assert portfolio.snapshot({"X": 100}).positions[0].quantity == 60

    retry_orders = build_liquidation_orders(portfolio, {"X": 100})
    second = execute_liquidation_orders(portfolio, retry_orders, simulator, {"X": 100}, 20, books={"X": book})
    assert second[0].remaining_quantity == 0
    assert portfolio.snapshot({"X": 100}).positions[0].quantity == 20
    assert sum(t.quantity for t in portfolio.trades if t.order_id == "liquidation:X") == 80


def test_non_risk_reducing_forced_order_is_rejected_without_mutation():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=1.0, maintenance_margin_rate=0.5))
    portfolio._positions["X"] = Position("X", 100, 1_000)
    from app.backtesting.execution import SimOrder

    order = SimOrder("forced:X", "X", ExecutionSide.BUY, 10)
    before = portfolio.export_state()
    result = execute_liquidation_orders(portfolio, (order,), ExecutionSimulator(), {"X": 1_000}, 10)

    assert result[0].rejected
    assert result[0].reason == "order is not risk-reducing"
    assert portfolio.export_state() == before
