from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventReplayConfig, EventType, MarketEvent
from app.backtesting.execution import ExecutionSimulator, ExecutionSide, SimOrder
from app.backtesting.portfolio import Portfolio
from app.backtesting.strategy import StrategyDecision


def test_replay_latency_delays_order_until_a_later_market_event():
    class Strategy:
        strategy_id = "replay-latency"
        strategy_version = "1"

        def on_event(self, event, context):
            if event.timestamp_ns == 100:
                return StrategyDecision(
                    action="BUY",
                    orders=(SimOrder("latency-order", "NIFTY", ExecutionSide.BUY, 1),),
                )
            return None

    engine = EventBacktestEngine(
        EventReplayConfig(latency_ns=50),
        execution=ExecutionSimulator(),
        portfolio=Portfolio(initial_cash=10_000),
    )
    result = engine.run(
        [
            MarketEvent(100, "NIFTY", EventType.QUOTE, {"bid": 99.0, "ask": 100.0}),
            MarketEvent(120, "NIFTY", EventType.QUOTE, {"bid": 109.0, "ask": 110.0}),
            MarketEvent(150, "NIFTY", EventType.QUOTE, {"bid": 119.0, "ask": 120.0}),
        ],
        Strategy(),
    )

    assert result.fills == 1
    assert result.final_snapshot is not None
    assert result.final_snapshot.cash == 9_880
    assert engine.order_states["latency-order"].status.value == "FILLED"
    assert engine.order_states["latency-order"].events[-1].timestamp_ns == 150
