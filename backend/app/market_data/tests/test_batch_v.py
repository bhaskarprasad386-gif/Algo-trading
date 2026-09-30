from app.market_data.strategy_runtime import StrategyRuntime, StrategyState
from app.market_data.strategy_manifest import StrategyManifest


class FakeFeed:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.callback = None

    def start(self, descriptors, callback):
        self.started += 1
        self.callback = callback
        return tuple(d.key for d in descriptors)

    def stop(self):
        self.stopped += 1


class D:
    def __init__(self):
        from app.market_data.contracts import InstrumentKey
        self.key = InstrumentKey("NFO", "NFO", "1")


def test_runtime_requires_live_enable_and_isolates_callback_errors():
    feed = FakeFeed()
    runtime = StrategyRuntime(StrategyManifest("demo", "Demo", live_enabled=True), feed=feed)
    seen = []

    runtime.start([D()], lambda record: (_ for _ in ()).throw(RuntimeError("boom")))
    assert runtime.state == StrategyState.RUNNING
    feed.callback(object())
    snap = runtime.snapshot()
    assert snap.events == 0
    assert snap.errors == 1
    assert "boom" in snap.last_error


def test_runtime_stop_is_idempotent_for_feed_lifecycle():
    feed = FakeFeed()
    runtime = StrategyRuntime(StrategyManifest("demo", "Demo", live_enabled=True), feed=feed)
    runtime.start([D()], lambda _: None)
    runtime.stop()
    runtime.stop()
    assert runtime.state == StrategyState.STOPPED
    assert feed.stopped == 2


def test_disabled_live_strategy_cannot_start():
    runtime = StrategyRuntime(StrategyManifest("demo", "Demo", live_enabled=False), feed=FakeFeed())
    try:
        runtime.start([D()], lambda _: None)
    except RuntimeError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("disabled strategy unexpectedly started")
