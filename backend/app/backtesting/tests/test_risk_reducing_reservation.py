from app.backtesting.events import EventType, MarketEvent
from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.execution import ExecutionSide, ExecutionSimulator, SimFill, SimOrder
from app.backtesting.portfolio import Portfolio, RiskConfig
from app.backtesting.strategy import StrategyDecision


def fill(order_id, instrument, side, quantity, price):
    return SimFill(order_id, instrument, side, quantity, price, 1, 0.0)


def test_risk_reducing_close_does_not_need_incremental_margin_reservation():
    p = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    p.apply_fill(fill("seed", "X", ExecutionSide.BUY, 300, 500))

    class Strategy:
        strategy_id = "close"
        strategy_version = "1"
        def on_event(self, event, context):
            return StrategyDecision(action="SELL", orders=(SimOrder("close", "X", ExecutionSide.SELL, 300),))

    e = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=p)
    r = e.run([MarketEvent(1_000, "X", EventType.QUOTE, {"bid": 100.0, "ask": 101.0})], Strategy())
    assert r.fills == 1
    assert p.snapshot({"X": 100}).positions == ()
    assert p.reserved_margin == 0
