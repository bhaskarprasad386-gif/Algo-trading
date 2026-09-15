from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent
from app.backtesting.strategy import StrategyDecision


def test_resumed_event_strategy_receives_pre_resume_history():
    events = [
        MarketEvent(1, "NIFTY", EventType.TRADE, {"price": 100}, sequence=1),
        MarketEvent(2, "NIFTY", EventType.TRADE, {"price": 101}, sequence=2),
        MarketEvent(3, "NIFTY", EventType.TRADE, {"price": 102}, sequence=3),
    ]
    histories = []

    class Strategy:
        strategy_id = "resume-history"
        strategy_version = "1"

        def on_start(self, context):
            histories.append(("start", tuple(e.timestamp_ns for e in context.history)))

        def on_event(self, event, context):
            histories.append((event.timestamp_ns, tuple(e.timestamp_ns for e in context.history)))
            return StrategyDecision(action="OBSERVE")

    result = EventBacktestEngine().run(events, Strategy(), start_event_index=2)

    assert histories == [
        ("start", (1, 2)),
        (3, (1, 2)),
    ]
    assert result.events_seen == 3
    assert result.events_dispatched == 1
    assert result.first_timestamp_ns == 3
    assert result.last_timestamp_ns == 3
