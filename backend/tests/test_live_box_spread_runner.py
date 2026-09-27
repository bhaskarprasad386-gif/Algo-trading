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

    monkeypatch.setattr(mod, "select_box_contracts", select)
    monkeypatch.setattr(mod, "concrete_strikes_from_master", lambda *a, **k: {})
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
    assert captured == ["30SEP2026"]
