from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner


def test_box_runner_retries_transient_session_failure(monkeypatch):
    runner = LiveBoxSpreadRunner(
        "memory.db",
        (),
        allowed_stock_symbols=(),
    )
    calls = []

    def run_session():
        calls.append("session")
        if len(calls) == 1:
            raise RuntimeError("transient")
        runner.stop_event.set()

    monkeypatch.setattr(runner, "_run_session", run_session)
    monkeypatch.setattr(runner.stop_event, "wait", lambda _seconds: False)

    runner.run_forever()

    assert calls == ["session", "session"]

def test_box_session_failure_cleans_up_without_disabling_supervisor(monkeypatch):
    import pytest
    import app.market_data.live_box_spread_runner as mod

    class Master:
        instruments = []

        def download(self):
            pass

    class Tracker:
        def __init__(self, *args, **kwargs):
            pass

        def atm(self, symbol, now_ns, **kwargs):
            return 100.0

    feeds = []

    class Feed:
        def __init__(self, *args, **kwargs):
            self.stopped = False
            feeds.append(self)

        def run_forever(self):
            pass

        def stop(self):
            self.stopped = True

        def snapshot(self):
            return {}

    class InlineThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(mod, "LiveSyntheticAtmTracker", Tracker)
    monkeypatch.setattr(
        mod, "concrete_strikes_from_master",
        lambda *args, **kwargs: {"ABC": (100.0, 110.0)},
    )
    monkeypatch.setattr(mod, "LiveSyntheticUnderlyingFeed", Feed)
    monkeypatch.setattr(mod, "Thread", InlineThread)

    def fail_selection(*args, **kwargs):
        raise RuntimeError("transient contract-selection failure")

    monkeypatch.setattr(mod, "select_box_contracts", fail_selection)
    runner = LiveBoxSpreadRunner(
        "memory.db",
        [mod.BoxSpreadLiveTarget("ABC", "STOCK", expiry="29OCT2026")],
        allowed_stock_symbols={"ABC"},
        future_master=Master(),
        auth=object(),
    )

    with pytest.raises(RuntimeError, match="transient contract-selection failure"):
        runner._run_session()

    assert not runner.stop_event.is_set()
    assert feeds and feeds[0].stopped
    assert runner.feed is None

