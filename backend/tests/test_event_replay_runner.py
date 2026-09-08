from app.backtesting.event_replay import ReplayEvent
from app.backtesting.event_replay_runner import EventReplayRunner
from app.backtesting.event_strategy import StrategyContext, StrategySignal


class BuyOnEvent:
    def on_event(self, context: StrategyContext):
        return StrategySignal("BUY", 1, str(context.data["id"]))


def test_event_replay_orders_by_nanosecond_timestamp_and_sequence():
    events = [
        ReplayEvent(2_000_000, 0, {"id": "late", "instrument": "A", "price": 12}),
        ReplayEvent(1_000_000, 2, {"id": "second", "instrument": "A", "price": 10}),
        ReplayEvent(1_000_000, 1, {"id": "first", "instrument": "A", "price": 9}),
    ]
    results = EventReplayRunner().run(events, strategy=BuyOnEvent())
    assert [r.signal.reason for r in results] == ["first", "second", "late"]
    assert [r.fills[0].filled_at_ns for r in results] == [1_000_000, 1_000_000, 2_000_000]
