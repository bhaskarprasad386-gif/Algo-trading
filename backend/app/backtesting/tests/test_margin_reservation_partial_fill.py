import pytest

from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent
from app.backtesting.execution import ExecutionSide, ExecutionSimulator, TimeInForce, SimFill, SimOrder
from app.backtesting.portfolio import Portfolio, RiskConfig
from app.backtesting.strategy import StrategyDecision


def fill(order_id, instrument, side, quantity, price):
    return SimFill(order_id, instrument, side, quantity, price, 1, 0.0)


def test_partial_fill_releases_only_filled_fraction_of_reserved_margin():
    class Strategy:
        strategy_id = "partial-reserve"
        strategy_version = "1"
        def on_event(self, event, context):
            return StrategyDecision(action="BUY", orders=(SimOrder("o1", "X", ExecutionSide.BUY, 10, time_in_force=TimeInForce.IOC),))

    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.2))
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([
        MarketEvent(1_000, "X", EventType.DEPTH, {"asks": [[100.0, 3]], "bids": [[99.0, 5]]})
    ], Strategy())

    assert result.fills == 1
    assert result.final_snapshot.positions[0].quantity == 3
    assert result.final_snapshot.reserved_margin == pytest.approx(0.0)
    assert engine.order_states["o1"].remaining_quantity == 7


def test_cancel_releases_residual_reserved_margin():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.2))
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    engine._update_market_state(MarketEvent(1_000, "X", EventType.QUOTE, {"bid": 99.0, "ask": 100.0}))
    order = SimOrder("rest", "X", ExecutionSide.BUY, 10)
    engine._lifecycle(order, 1_000)
    portfolio.reserve_margin("rest", 200.0)
    engine._reserved_margin["rest"] = 200.0
    engine._open_orders["rest"] = order

    engine.cancel_order("rest", 2_000)

    assert portfolio.reserved_margin == pytest.approx(0.0)
    assert "rest" not in engine.open_orders


def test_atomic_multi_leg_risk_failure_restores_reservations():
    class Strategy:
        strategy_id = "atomic-reservation"
        strategy_version = "1"
        def on_event(self, event, context):
            if event.instrument != "B":
                return None
            return StrategyDecision(action="SPREAD", orders=(
                SimOrder("a", "A", ExecutionSide.BUY, 5),
                SimOrder("b", "B", ExecutionSide.BUY, 6),
            ))

    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.1, max_position_quantity=5))
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([
        MarketEvent(1, "A", EventType.QUOTE, {"bid": 99, "ask": 100}),
        MarketEvent(2, "B", EventType.QUOTE, {"bid": 199, "ask": 200}),
    ], Strategy())

    assert result.fills == 0
    assert result.risk_blocks == 1
    assert portfolio.reserved_margin == pytest.approx(0.0)
    assert engine.open_orders == {}
