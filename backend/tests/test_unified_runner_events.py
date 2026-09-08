from app.backtesting.event_strategy import StrategySignal
from app.backtesting.unified_runner import UnifiedStrategyRunner


class NewsStrategy:
    def on_event(self, context):
        if context.data.get("event") == "POSITIVE":
            return StrategySignal("BUY", 2, "news")
        return None


def test_unified_runner_preserves_event_timestamp_and_payload():
    runner = UnifiedStrategyRunner()
    signals = runner.run_events(
        [{"timestamp_ns": 1_000_000, "event": "POSITIVE", "source": "news"}],
        strategy=NewsStrategy(),
    )
    assert len(signals) == 1
    assert signals[0].action == "BUY"
    assert signals[0].quantity == 2


def test_unified_runner_rejects_missing_event_timestamp():
    runner = UnifiedStrategyRunner()
    try:
        runner.run_events([{"event": "POSITIVE"}], strategy=NewsStrategy())
    except ValueError as exc:
        assert "timestamp_ns" in str(exc)
    else:
        raise AssertionError("missing timestamp must fail closed")
