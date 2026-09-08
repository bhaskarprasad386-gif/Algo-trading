from app.backtesting.event_strategy import StrategyAdapter, StrategyContext, StrategySignal


class BuyOnEvent:
    def on_event(self, context: StrategyContext):
        if context.data.get("event") == "signal":
            return StrategySignal("BUY", quantity=1, reason="event")
        return None


def test_event_strategy_adapter_preserves_point_in_time_event():
    adapter = StrategyAdapter(BuyOnEvent())
    signal = adapter.on_event(timestamp_ns=123456789, data={"event": "signal"})

    assert signal == StrategySignal("BUY", quantity=1, reason="event")
