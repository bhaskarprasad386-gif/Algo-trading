import pytest

from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent
from app.backtesting.execution import ExecutionSide, ExecutionSimulator, SimFill, SimOrder
from app.backtesting.order_lifecycle import OrderStatus
from app.backtesting.portfolio import Portfolio, RiskConfig
from app.backtesting.strategy import StrategyDecision


def fill(order_id, instrument, side, quantity, price):
    return SimFill(order_id, instrument, side, quantity, price, 1, 0.0)


def test_event_engine_blocks_new_risk_after_mark_to_market_margin_breach():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    portfolio.apply_fill(fill("seed", "X", ExecutionSide.BUY, 300, 500))

    class Strategy:
        strategy_id = "margin-block"
        strategy_version = "1"

        def on_event(self, event, context):
            return StrategyDecision(action="BUY", orders=(SimOrder("add", "X", ExecutionSide.BUY, 1),))

    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([MarketEvent(1_000, "X", EventType.TRADE, {"price": 100.0})], Strategy())

    assert result.fills == 0
    assert result.risk_blocks == 1
    assert engine.order_states["add"].status == OrderStatus.REJECTED
    assert portfolio.snapshot({"X": 100}).margin_call is True


def test_event_engine_allows_risk_reducing_order_during_margin_breach():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    portfolio.apply_fill(fill("seed", "X", ExecutionSide.BUY, 300, 500))

    class Strategy:
        strategy_id = "margin-unwind"
        strategy_version = "1"

        def on_event(self, event, context):
            return StrategyDecision(action="SELL", orders=(SimOrder("close", "X", ExecutionSide.SELL, 300),))

    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([MarketEvent(1_000, "X", EventType.QUOTE, {"bid": 100.0, "ask": 101.0})], Strategy())

    assert result.fills == 1
    assert result.risk_blocks == 0
    assert engine.order_states["close"].status == OrderStatus.FILLED
    assert portfolio.snapshot({"X": 100}).positions == ()


def test_existing_order_is_rejected_when_new_mark_causes_margin_breach():
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    portfolio.apply_fill(fill("seed", "X", ExecutionSide.BUY, 300, 500))

    class Strategy:
        strategy_id = "existing-order-risk"
        strategy_version = "1"

        def on_event(self, event, context):
            if event.timestamp_ns == 1_000:
                return StrategyDecision(action="BUY", orders=(SimOrder("resting", "Y", ExecutionSide.BUY, 1),))
            return None

    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([
        MarketEvent(1_000, "X", EventType.TRADE, {"price": 500.0}),
        MarketEvent(2_000, "X", EventType.TRADE, {"price": 100.0}),
    ], Strategy())

    assert result.fills == 0
    assert result.risk_blocks >= 1
    assert engine.order_states["resting"].status == OrderStatus.REJECTED
    assert portfolio.snapshot({"X": 100}).positions[0].quantity == 300
