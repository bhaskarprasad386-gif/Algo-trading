from app.market_data.box_spread_subscriptions import BoxContractSelection


def test_box_runner_uses_resolved_expiry_when_target_has_none(monkeypatch):
    import app.market_data.live_box_spread_runner as mod

    class Master:
        _loaded = True
        instruments = [
            {"exch_seg":"NFO","name":"ABC","instrumenttype":"FUTSTK","expiry":"30SEP2026"},
            {"exch_seg":"NFO","name":"ABC","instrumenttype":"FUTSTK","expiry":"26NOV2026"},
        ]
        def download(self):
            raise AssertionError("already-loaded master must not be downloaded twice")

    captured = []
    def select(*args, **kwargs):
        captured.append(kwargs["expiry"])
        raise RuntimeError("stop-after-selection")

    class Tracker:
        def __init__(self, *args, **kwargs): pass
        def atm(self, symbol, now_ns, **kwargs): return 100.0

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
        runner._run_session()
    except RuntimeError:
        pass
    assert captured == ["26NOV2026"]


def test_box_runner_does_not_gate_ready_underlyings_on_missing_atm(monkeypatch):
    import app.market_data.live_box_spread_runner as mod

    class Master:
        instruments = []
        def download(self): pass

    class Tracker:
        def __init__(self, *args, **kwargs): pass
        def atm(self, symbol, now_ns, **kwargs): return 100.0 if symbol == "READY" else None

    class Feed:
        def __init__(self, *args, **kwargs): pass
        def run_forever(self): pass
        def stop(self): pass

    captured = []
    def select(*args, **kwargs):
        captured.append(kwargs["underlying"])
        return BoxContractSelection((object(),), "26NOV2026", 100.0)

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


def test_box_atm_does_not_refresh_while_inside_subscribed_strike_coverage():
    from app.market_data.live_box_spread_runner import _atm_outside_subscribed_range

    coverage = {90.0, 100.0, 110.0}
    assert _atm_outside_subscribed_range(90.0, coverage) is False
    assert _atm_outside_subscribed_range(100.0, coverage) is False
    assert _atm_outside_subscribed_range(110.0, coverage) is False
    assert _atm_outside_subscribed_range(120.0, coverage) is True
    assert _atm_outside_subscribed_range(80.0, coverage) is True
    assert _atm_outside_subscribed_range(None, coverage) is False


def test_nifty_weekly_option_expiry_seeds_atm_tracker_without_future_expiry(monkeypatch):
    import app.market_data.live_box_spread_runner as mod

    class Master:
        instruments = [
            {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "22OCT2026", "strike": "10000", "symbol": "NIFTY100CE", "token": "0"},
            {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "26NOV2026", "strike": "10000", "symbol": "NIFTY100CE", "token": "1"},
            {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "05NOV2026", "strike": "10000", "symbol": "NIFTY100CE", "token": "2"},
        ]
        def download(self): pass

    seeded = []
    def concrete(*args, **kwargs):
        seeded.append(kwargs.get("expiry"))
        return {"NIFTY": (100.0, 110.0)}

    class Tracker:
        def __init__(self, *args, **kwargs): pass
        def atm(self, symbol, now_ns, **kwargs): return 100.0

    class Feed:
        def __init__(self, *args, **kwargs): pass
        def run_forever(self): pass
        def stop(self): pass

    selected_expiries = []
    def stop_after_selection(*args, **kwargs):
        selected_expiries.append(kwargs.get("expiry"))
        raise RuntimeError("stop-after-selection")

    monkeypatch.setattr(mod, "concrete_strikes_from_master", concrete)
    monkeypatch.setattr(mod, "LiveSyntheticAtmTracker", Tracker)
    monkeypatch.setattr(mod, "LiveSyntheticUnderlyingFeed", Feed)
    monkeypatch.setattr(mod, "select_box_contracts", stop_after_selection)
    runner = mod.LiveBoxSpreadRunner(
        object(),
        [mod.BoxSpreadLiveTarget("NIFTY", "INDEX")],
        allowed_stock_symbols=set(),
        future_master=Master(),
        auth=object(),
    )
    try:
        runner._run_session()
    except RuntimeError as exc:
        assert str(exc) == "stop-after-selection"
    assert seeded == ["05NOV2026"]
    assert selected_expiries == ["05NOV2026"]


def test_sensex_weekly_expiry_uses_bfo_option_master_without_future_contracts(monkeypatch):
    import app.market_data.live_box_spread_runner as mod

    class Master:
        instruments = [
            {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "OPTIDX", "expiry": "22OCT2026", "strike": "10000"},
            {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "OPTIDX", "expiry": "26NOV2026", "strike": "10000"},
            {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "OPTIDX", "expiry": "05NOV2026", "strike": "10000"},
        ]
        def download(self): pass

    seeded = []
    def concrete(*args, **kwargs):
        seeded.append((kwargs.get("expiry"), kwargs.get("exchange_segment")))
        return {"SENSEX": (100.0, 110.0)}

    class Tracker:
        def __init__(self, *args, **kwargs): pass
        def atm(self, symbol, now_ns, **kwargs): return 100.0

    class Feed:
        def __init__(self, *args, **kwargs): pass
        def run_forever(self): pass
        def stop(self): pass

    selected = []
    def stop_after_selection(*args, **kwargs):
        selected.append((kwargs.get("expiry"), kwargs.get("underlying")))
        raise RuntimeError("stop-after-selection")

    monkeypatch.setattr(mod, "concrete_strikes_from_master", concrete)
    monkeypatch.setattr(mod, "LiveSyntheticAtmTracker", Tracker)
    monkeypatch.setattr(mod, "LiveSyntheticUnderlyingFeed", Feed)
    monkeypatch.setattr(mod, "select_box_contracts", stop_after_selection)
    runner = mod.LiveBoxSpreadRunner(
        object(),
        [mod.BoxSpreadLiveTarget("SENSEX", "INDEX")],
        allowed_stock_symbols=set(),
        future_master=Master(),
        auth=object(),
    )
    try:
        runner._run_session()
    except RuntimeError as exc:
        assert str(exc) == "stop-after-selection"

    assert seeded == [("05NOV2026", "BFO")]
    assert selected == [("05NOV2026", "SENSEX")]


def test_nifty_and_sensex_targets_are_ordered_before_other_indices_and_stocks():
    import app.market_data.live_box_spread_runner as mod

    runner = mod.LiveBoxSpreadRunner(
        object(),
        [
            mod.BoxSpreadLiveTarget("ABC", "STOCK"),
            mod.BoxSpreadLiveTarget("BANKNIFTY", "INDEX"),
            mod.BoxSpreadLiveTarget("SENSEX", "INDEX"),
            mod.BoxSpreadLiveTarget("NIFTY", "INDEX"),
        ],
        allowed_stock_symbols={"ABC"},
        future_master=object(),
        auth=object(),
    )

    assert [target.underlying for target in runner.targets] == [
        "NIFTY", "SENSEX", "ABC", "BANKNIFTY"
    ]


def test_box_runner_unions_atm_strikes_across_weekly_and_monthly_targets(monkeypatch):
    import app.market_data.live_box_spread_runner as mod

    class Master:
        instruments = []
        def download(self): pass

    seeded = {}
    class Tracker:
        def __init__(self, *, strikes_by_symbol):
            seeded.update(strikes_by_symbol)
        def atm(self, symbol, now_ns, **kwargs): return 100.0

    class Feed:
        def __init__(self, *args, **kwargs): pass
        def run_forever(self): pass
        def stop(self): pass

    selected = []
    def concrete(*args, **kwargs):
        return {"NIFTY": (100.0, 110.0)} if kwargs["expiry"] == "05NOV2026" else {"NIFTY": (100.0, 120.0)}

    def stop_after_selection(*args, **kwargs):
        selected.append(kwargs["expiry"])
        raise RuntimeError("stop-after-selection")

    monkeypatch.setattr(mod, "concrete_strikes_from_master", concrete)
    monkeypatch.setattr(mod, "LiveSyntheticAtmTracker", Tracker)
    monkeypatch.setattr(mod, "LiveSyntheticUnderlyingFeed", Feed)
    monkeypatch.setattr(mod, "select_box_contracts", stop_after_selection)
    runner = mod.LiveBoxSpreadRunner(
        object(),
        [
            mod.BoxSpreadLiveTarget("NIFTY", "INDEX", "05NOV2026"),
            mod.BoxSpreadLiveTarget("NIFTY", "INDEX", "26NOV2026"),
        ],
        allowed_stock_symbols=set(),
        future_master=Master(),
        auth=object(),
    )

    try:
        runner._run_session()
    except RuntimeError as exc:
        assert str(exc) == "stop-after-selection"

    assert seeded["NIFTY"] == (100.0, 110.0, 120.0)
    assert selected == ["05NOV2026"]


def test_box_runner_depth_shadow_is_disabled_by_default(monkeypatch):
    import app.market_data.live_box_spread_runner as mod

    monkeypatch.setattr(mod.settings, "LIVE_BOX_SPREAD_DEPTH_SHADOW_ENABLED", False)
    runner = mod.LiveBoxSpreadRunner(
        object(), [mod.BoxSpreadLiveTarget("NIFTY", "INDEX")],
        allowed_stock_symbols=set(), future_master=object(), auth=object(),
    )
    state = runner.snapshot()["depth_shadow"]
    assert state["enabled"] is False
    assert state["running"] is False
    assert state["record_count"] == 0
    assert state["comparison_diagnostics"]["status_counts"] == {}
    assert state["comparison_diagnostics"]["complete_difference_sample"]["count"] == 0


def test_box_runner_starts_and_stops_only_separate_depth_consumer_when_enabled(monkeypatch):
    import app.market_data.live_box_spread_runner as mod
    from app.market_data.contracts import InstrumentKey
    from app.market_data.registry import InstrumentDescriptor

    monkeypatch.setattr(mod.settings, "LIVE_BOX_SPREAD_DEPTH_SHADOW_ENABLED", True)
    descriptor = InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", "123"), symbol="NIFTY06NOV2625000CE",
        instrument_type="option", exchange="NFO", segment="NFO",
        expiry="06NOV2026", strike=25000, option_type="CE", lot_size=65,
        underlying="NIFTY",
    )
    selected = []

    class Master:
        _loaded = True
        instruments = []
        def download(self): pass

    class Tracker:
        def __init__(self, *args, **kwargs): pass
        def atm(self, symbol, now_ns, **kwargs): return 25000.0

    class Feed:
        def __init__(self, *args, **kwargs): pass
        def run_forever(self): pass
        def stop(self): pass

    class Shadow:
        def __init__(self, *, enabled):
            self.enabled = enabled
            self.started = []
            self.stopped = 0
        def start(self, descriptors):
            self.started.append(tuple(descriptors))
        def stop(self):
            self.stopped += 1
        def snapshot(self):
            return {"running": bool(self.started), "record_count": 0, "counts": {}}
        def compare_opportunity(self, opportunity):
            return {"status": "INCOMPLETE", "reason": "MISSING_LEG"}

    monkeypatch.setattr(mod, "BoxLiveDepthShadowFeed", Shadow)
    monkeypatch.setattr(mod, "concrete_strikes_from_master", lambda *a, **k: {"NIFTY": (25000.0,)})
    monkeypatch.setattr(mod, "LiveSyntheticAtmTracker", Tracker)
    monkeypatch.setattr(mod, "LiveSyntheticUnderlyingFeed", Feed)
    monkeypatch.setattr(mod, "select_box_contracts", lambda *a, **k: BoxContractSelection((descriptor,), "06NOV2026", 25000.0))
    monkeypatch.setattr(mod, "LiveBoxSpreadPipeline", lambda *a, **k: type("P", (), {"observe": lambda self, payload: ()})())

    class Recorder:
        def __init__(self, *args, **kwargs): pass
        def run_forever(self):
            runner.stop_event.set()
        def stop(self): pass
        def snapshot(self): return {"running": True}

    monkeypatch.setattr(mod, "LiveSyntheticOptionFutureRecorder", Recorder)
    monkeypatch.setattr(mod, "sleep", lambda _seconds: None)
    runner = mod.LiveBoxSpreadRunner(
        object(), [mod.BoxSpreadLiveTarget("NIFTY", "INDEX")],
        allowed_stock_symbols=set(), future_master=Master(), auth=object(),
    )
    runner._run_session()
    assert runner.depth_shadow.started == [(descriptor,)]
    assert runner.depth_shadow.stopped >= 1


def test_box_underlying_health_is_degraded_when_any_configured_symbol_is_stale():
    from app.market_data.live_box_spread_runner import _box_underlying_health_snapshot

    snapshot = _box_underlying_health_snapshot({
        "state": "LIVE",
        "state_reason": "at_least_one_underlying_has_a_fresh_tick",
        "symbols": {
            "NIFTY": {"state": "LIVE"},
            "BANKNIFTY": {"state": "LIVE"},
            "FINNIFTY": {"state": "LIVE"},
            "MIDCPNIFTY": {"state": "LIVE"},
            "SENSEX": {"state": "STALE"},
            "BANKEX": {"state": "STALE"},
        },
    })

    assert snapshot["state"] == "DEGRADED"
    assert snapshot["state_reason"] == "one_or_more_configured_underlyings_are_not_fresh"
    assert snapshot["fresh_symbol_count"] == 4
    assert snapshot["stale_symbol_count"] == 2
    assert snapshot["configured_symbol_count"] == 6


def test_box_underlying_health_is_live_only_when_every_configured_symbol_is_fresh():
    from app.market_data.live_box_spread_runner import _box_underlying_health_snapshot

    snapshot = _box_underlying_health_snapshot({
        "state": "DEGRADED",
        "symbols": {name: {"state": "LIVE"} for name in (
            "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"
        )},
    })

    assert snapshot["state"] == "LIVE"
    assert snapshot["state_reason"] == "all_configured_underlyings_have_fresh_ticks"
    assert snapshot["fresh_symbol_count"] == 6


def test_box_underlying_health_never_promotes_a_feed_with_no_fresh_symbols():
    from app.market_data.live_box_spread_runner import _box_underlying_health_snapshot

    snapshot = _box_underlying_health_snapshot({
        "state": "LIVE",
        "symbols": {
            "SENSEX": {"state": "STALE"},
            "BANKEX": {"state": "WAITING_FOR_TICK"},
        },
    })

    assert snapshot["state"] == "STALE"
    assert snapshot["fresh_symbol_count"] == 0
    assert snapshot["stale_symbol_count"] == 1
    assert snapshot["waiting_symbol_count"] == 1



def test_box_spread_excludes_finnifty_and_bankex_from_scanner_universe():
    from app.market_data.live_box_spread_runner import BOX_SPREAD_EXCLUDED_INDEX_SYMBOLS

    assert BOX_SPREAD_EXCLUDED_INDEX_SYMBOLS == frozenset({"FINNIFTY", "BANKEX"})
    configured = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
    subscribed = configured - BOX_SPREAD_EXCLUDED_INDEX_SYMBOLS
    assert subscribed == {"NIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX"}
