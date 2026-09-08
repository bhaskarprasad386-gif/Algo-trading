from app.backtesting.event_strategy import StrategySignal
from app.backtesting.unified_event_runner import UnifiedEventRunner


class BuyOnTick:
    def on_event(self, context):
        if context.data.get("price", 0) > 100:
            return StrategySignal("BUY", 1, "threshold")
        return None


def test_event_runner_preserves_tick_timestamp_and_payload():
    seen = []
    count = UnifiedEventRunner().run(
        [(10, {"price": 99}), (20, {"price": 101})],
        strategy=BuyOnTick(),
        on_signal=lambda signal: seen.append(signal),
    )
    assert count == 1
    assert seen[0].action == "BUY"
    assert seen[0].quantity == 1


def test_event_runner_rejects_out_of_order_ticks():
    try:
        UnifiedEventRunner().run([(20, {}), (10, {})], strategy=BuyOnTick())
    except ValueError as exc:
        assert "timestamp order" in str(exc)
    else:
        raise AssertionError("out-of-order events must fail closed")
