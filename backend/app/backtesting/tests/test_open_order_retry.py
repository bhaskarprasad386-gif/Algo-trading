import pytest

from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent
from app.backtesting.execution import ExecutionSimulator, ExecutionSide, OrderType, SimOrder, TimeInForce
from app.backtesting.order_lifecycle import OrderStatus
from app.backtesting.portfolio import Portfolio
from app.backtesting.strategy import StrategyDecision


def test_limit_order_partial_fill_retries_on_next_depth_event():
    class Strategy:
        strategy_id = "retry-partial"
        strategy_version = "1"
        def on_event(self, event, context):
            if event.sequence == 1:
                return StrategyDecision(action="BUY", orders=(SimOrder("limit-1", "NIFTY", ExecutionSide.BUY, 10, order_type=OrderType.LIMIT, limit_price=100.0, time_in_force=TimeInForce.GTC),))
            return None

    portfolio = Portfolio(100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    events = [
        MarketEvent(1_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 3]], "bids": [[99.0, 5]]}, sequence=1),
        MarketEvent(2_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 7]], "bids": [[99.0, 5]]}, sequence=2),
    ]
    result = engine.run(events, Strategy())
    assert result.fills == 2
    assert result.final_snapshot.positions[0].quantity == 10
    assert engine.order_states["limit-1"].status == OrderStatus.FILLED
    assert engine.open_orders == {}
    assert result.final_snapshot.reserved_margin == pytest.approx(0)


def test_stop_order_submitted_before_trigger_fills_on_later_event():
    class Strategy:
        strategy_id = "retry-stop"
        strategy_version = "1"
        def on_event(self, event, context):
            if event.sequence == 1:
                return StrategyDecision(action="BUY", orders=(SimOrder("stop-1", "NIFTY", ExecutionSide.BUY, 2, order_type=OrderType.STOP, stop_price=101.0, time_in_force=TimeInForce.GTC),))
            return None

    portfolio = Portfolio(100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    events = [
        MarketEvent(1_000, "NIFTY", EventType.TRADE, {"price": 100.0}, sequence=1),
        MarketEvent(2_000, "NIFTY", EventType.TRADE, {"price": 101.0}, sequence=2),
    ]
    result = engine.run(events, Strategy())
    assert result.fills == 1
    assert engine.order_states["stop-1"].status == OrderStatus.FILLED
    assert result.final_snapshot.positions[0].quantity == 2


def test_ioc_residual_does_not_retry_on_next_event():
    class Strategy:
        strategy_id = "retry-ioc"
        strategy_version = "1"
        def on_event(self, event, context):
            if event.sequence == 1:
                return StrategyDecision(action="BUY", orders=(SimOrder("ioc-1", "NIFTY", ExecutionSide.BUY, 10, time_in_force=TimeInForce.IOC),))
            return None

    portfolio = Portfolio(100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    events = [
        MarketEvent(1_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 3]], "bids": [[99.0, 5]]}, sequence=1),
        MarketEvent(2_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 7]], "bids": [[99.0, 5]]}, sequence=2),
    ]
    result = engine.run(events, Strategy())
    assert result.fills == 1
    assert result.final_snapshot.positions[0].quantity == 3
    assert engine.order_states["ioc-1"].status == OrderStatus.CANCELLED
    assert engine.open_orders == {}
