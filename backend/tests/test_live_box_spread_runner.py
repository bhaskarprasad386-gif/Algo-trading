from app.market_data.box_spread_subscriptions import BoxContractSelection


def test_box_runner_uses_resolved_expiry_when_target_has_none(monkeypatch):
    import app.market_data.live_box_spread_runner as mod

    class Master:
        instruments = [
            {"exch_seg":"NFO","name":"ABC","instrumenttype":"FUTSTK","expiry":"30SEP2026"},
            {"exch_seg":"NFO","name":"ABC","instrumenttype":"FUTSTK","expiry":"29OCT2026"},
        ]
        def download(self): pass

    captured = []
    def select(*args, **kwargs):
        captured.append(kwargs["expiry"])
        raise RuntimeError("stop-after-selection")

    class Tracker:
        def __init__(self, *args, **kwargs): pass
        def atm(self, symbol, now_ns): return 100.0

    monkeypatch.setattr(mod, "select_box_contracts", select)
    monkeypatch.setattr(mod, "concrete_strikes_from_master", lambda *a, **k: {"ABC": (100.0, 110.0)})
    monkeypatch.setattr(mod, "LiveSyntheticAtmTracker", Tracker)
    monkeypatch.setattr(mod, "LiveSyntheticUnderlyingFeed", lambda *a, **k: type("F", (), {"run_forever":lambda self: None, "stop":lambda self: None})())
    runner = mod.LiveBoxSpreadRunner(
        object(),
        [mod.BoxSpreadLiveTarget("ABC", "STOCK")],
        allowed_stock_symbols={"ABC"},
        future_master=Master(),
        auth=object(),
    )
    try:
        runner.run_forever()
    except RuntimeError:
        pass
    assert captured == ["29OCT2026"]


def test_box_runner_does_not_gate_ready_underlyings_on_missing_atm(monkeypatch):
    import app.market_data.live_box_spread_runner as mod

    class Master:
        instruments = []
        def download(self): pass

    class Tracker:
        def __init__(self, *args, **kwargs): pass
        def atm(self, symbol, now_ns): return 100.0 if symbol == "READY" else None

    class Feed:
        def __init__(self, *args, **kwargs): pass
        def run_forever(self): pass
        def stop(self): pass

    captured = []
    def select(*args, **kwargs):
        captured.append(kwargs["underlying"])
        return BoxContractSelection((object(),), "29OCT2026", 100.0)

    monkeypatch.setattr(mod, "concrete_strikes_from_master", lambda *a, **k: {"READY": (100.0, 110.0), "WAITING": (100.0, 110.0)})
    monkeypatch.setattr(mod, "LiveSyntheticAtmTracker", Tracker)
    monkeypatch.setattr(mod, "LiveSyntheticUnderlyingFeed", Feed)
    monkeypatch.setattr(mod, "select_box_contracts", select)
    monkeypatch.setattr(mod, "LiveBoxSpreadPipeline", lambda *a, **k: type("P", (), {"observe": lambda self, payload: None})())
    class Recorder:
        def __init__(self, *args, **kwargs): self.kwargs = kwargs
        def run_forever(self):
            runner._readiness["state"] = "recorder_started"
            runner.stop_event.set()
        def stop(self): pass
        def snapshot(self): return {"running": True}
    monkeypatch.setattr(mod, "LiveSyntheticOptionFutureRecorder", Recorder)

    runner = mod.LiveBoxSpreadRunner(
        object(),
        [mod.BoxSpreadLiveTarget("READY", "STOCK"), mod.BoxSpreadLiveTarget("WAITING", "STOCK")],
        allowed_stock_symbols={"READY", "WAITING"},
        future_master=Master(),
        auth=object(),
    )
    monkeypatch.setattr(mod, "sleep", lambda _seconds: None)
    runner._run_session()
    assert captured == ["READY"]
    assert runner._readiness["state"] == "recorder_started"
