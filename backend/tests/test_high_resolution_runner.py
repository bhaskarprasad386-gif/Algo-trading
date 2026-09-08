from app.backtesting.event_strategy import StrategyContext, StrategySignal
from app.backtesting.event_replay import ReplayEvent
from app.backtesting.high_resolution_runner import HighResolutionEventRunner


class BuyOnPositiveTick:
    def on_event(self, context: StrategyContext):
        if context.data.get("buy"):
            return StrategySignal("BUY", 1, "tick")
        return None


def test_high_resolution_runner_orders_and_executes_events():
    events = [
        ReplayEvent(2_000_000, 1, {"price": 102.0, "buy": True}),
        ReplayEvent(1_000_000, 2, {"price": 100.0, "buy": True}),
    ]
    results = HighResolutionEventRunner().run(
        events, strategy=BuyOnPositiveTick(), instrument="NIFTY"
    )
    assert [r.fills[0].filled_at_ns for r in results] == [1_000_000, 2_000_000]
    assert [r.fills[0].price for r in results] == [100.0, 102.0]
