from app.market_data.strategy_runtime import StrategyRuntime, StrategyState
from app.market_data.strategy_runtime_registry import StrategyRuntimeRegistry
from app.market_data.strategy_manifest import StrategyManifest


class Feed:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self, descriptors, callback):
        self.started += 1
        return tuple(d.key for d in descriptors)

    def stop(self):
        self.stopped += 1


def test_registry_keeps_strategy_instances_isolated():
    registry = StrategyRuntimeRegistry()
    first = registry.create(StrategyManifest("one", "One", live_enabled=True), feed=Feed())
    second = registry.create(StrategyManifest("two", "Two", live_enabled=True), feed=Feed())
    assert registry.get("ONE") is first
    assert registry.get("two") is second
    assert registry.snapshots()[0].strategy_id == "one"
    assert registry.snapshots()[1].strategy_id == "two"


def test_registry_rejects_duplicate_strategy_id():
    registry = StrategyRuntimeRegistry()
    registry.create(StrategyManifest("demo", "Demo", live_enabled=True), feed=Feed())
    try:
        registry.create(StrategyManifest("DEMO", "Other", live_enabled=True), feed=Feed())
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate strategy runtime was accepted")


def test_registry_stop_all_and_clear_stopped():
    registry = StrategyRuntimeRegistry()
    one_feed = Feed()
    two_feed = Feed()
    registry.create(StrategyManifest("one", "One", live_enabled=True), feed=one_feed)
    registry.create(StrategyManifest("two", "Two", live_enabled=True), feed=two_feed)
    registry.start("one", [], lambda _: None)
    registry.start("two", [], lambda _: None)
    registry.stop_all()
    assert one_feed.stopped == 1
    assert two_feed.stopped == 1
    assert all(item.state == StrategyState.STOPPED for item in registry.snapshots())
    assert registry.clear_stopped() == 2
    assert registry.snapshots() == ()
