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
    from app.market_data.contracts import InstrumentKey
    from app.market_data.registry import InstrumentDescriptor

    def select(*args, **kwargs):
        underlying = kwargs["underlying"]
        captured.append(underlying)
        descriptor = InstrumentDescriptor(
            key=InstrumentKey("NFO", "NFO", underlying),
            symbol=f"{underlying}26NOV26100CE",
            instrument_type="option", exchange="NFO", segment="NFO",
            expiry="26NOV2026", strike=100, option_type="CE", lot_size=25,
            underlying=underlying,
        )
        return BoxContractSelection((descriptor,), "26NOV2026", 100.0)

    monkeypatch.setattr(mod, "concrete_strikes_from_master", lambda *a, **k: {"READY": (100.0, 110.0), "WAITING": (100.0, 110.0)})
    monkeypatch.setattr(mod, "LiveSyntheticAtmTracker", Tracker)
    monkeypatch.setattr(mod, "LiveSyntheticUnderlyingFeed", Feed)
    monkeypatch.setattr(mod, "select_box_contracts", select)
    monkeypatch.setattr(mod, "LiveBoxSpreadPipeline", lambda *a, **k: type("P", (), {"observe": lambda self, payload: None, "set_publish_enabled": lambda self, enabled: None})())
    class Recorder:
        def __init__(self, *args, **kwargs): self.kwargs = kwargs
        def run_forever(self):
            runner._readiness["state"] = "recorder_started"
            runner.stop_event.set()
        def stop(self): pass
        def snapshot(self): return {"running": True}
    monkeypatch.setattr(mod, "LiveSyntheticOptionFutureRecorder", Recorder)

    class Shadow:
        def __init__(self, *, enabled, consumer=None):
            self.enabled = enabled
            self.consumer = consumer
        def start(self, descriptors): pass
        def stop(self): pass
        def snapshot(self): return {"running": True, "record_count": 0, "counts": {}}
        def compare_opportunity(self, opportunity):
            return {"status": "INCOMPLETE", "reason": "MISSING_LEG"}

    monkeypatch.setattr(mod.settings, "LIVE_BOX_SPREAD_DEPTH_SHADOW_ENABLED", True)
    monkeypatch.setattr(mod, "BoxLiveDepthShadowFeed", Shadow)

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
        _loaded = True
        instruments = [
            {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "22OCT2026", "strike": "10000", "symbol": "NIFTY100CE", "token": "0"},
            {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "25NOV2027", "strike": "10000", "symbol": "NIFTY100CE", "token": "1"},
            {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "11NOV2027", "strike": "10000", "symbol": "NIFTY100CE", "token": "2"},
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
    assert seeded == ["11NOV2027"]
    assert selected_expiries == ["11NOV2027"]


def test_sensex_weekly_expiry_uses_bfo_option_master_without_future_contracts(monkeypatch):
    import app.market_data.live_box_spread_runner as mod

    class Master:
        _loaded = True
        instruments = [
            {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "OPTIDX", "expiry": "22OCT2026", "strike": "10000"},
            {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "OPTIDX", "expiry": "30NOV2027", "strike": "10000"},
            {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "OPTIDX", "expiry": "09NOV2027", "strike": "10000"},
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

    assert seeded == [("09NOV2027", "BFO")]
    assert selected == [("09NOV2027", "SENSEX")]


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
        def __init__(self, *, enabled, consumer=None):
            self.enabled = enabled
            self.consumer = consumer
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
    monkeypatch.setattr(mod, "LiveBoxSpreadPipeline", lambda *a, **k: type("P", (), {"observe": lambda self, payload: (), "set_publish_enabled": lambda self, enabled: None})())

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



def test_box_spread_excludes_midcpnifty_but_keeps_other_index_manual_selection():
    from app.market_data.live_box_spread_runner import BOX_SPREAD_EXCLUDED_INDEX_SYMBOLS

    assert BOX_SPREAD_EXCLUDED_INDEX_SYMBOLS == frozenset({"MIDCPNIFTY"})
    configured = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
    supported = configured - BOX_SPREAD_EXCLUDED_INDEX_SYMBOLS
    assert supported == {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX"}
    assert "MIDCPNIFTY" not in supported


def test_box_depth_shadow_contracts_are_mapped_to_option_descriptors():
    from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner
    from app.market_data.live_synthetic_stream import SyntheticSubscription

    contracts = (
        SyntheticSubscription(
            2, "101", "NIFTY26NOV2625000CE", "NIFTY", "INDEX",
            "26NOV2026", "CE", 25000.0, 65,
        ),
        SyntheticSubscription(
            4, "202", "SENSEX26NOV2675000PE", "SENSEX", "INDEX",
            "26NOV2026", "PE", 75000.0, 20,
        ),
    )

    descriptors = LiveBoxSpreadRunner._depth_shadow_descriptors(contracts)

    assert len(descriptors) == 2
    assert descriptors[0].key.exchange == "NFO"
    assert descriptors[0].instrument_type == "option"
    assert descriptors[0].underlying == "NIFTY"
    assert descriptors[0].option_type == "CE"
    assert descriptors[0].strike == 25000.0
    assert descriptors[1].key.exchange == "BFO"
    assert descriptors[1].instrument_type == "option"
    assert descriptors[1].underlying == "SENSEX"
    assert descriptors[1].option_type == "PE"
    assert descriptors[1].strike == 75000.0


def test_box_spread_index_edge_thresholds_match_configured_floors():
    from app.market_data.live_box_spread_runner import INDEX_EDGE_THRESHOLDS

    assert INDEX_EDGE_THRESHOLDS == {
        "NIFTY": 5.0,
        "BANKNIFTY": 10.0,
        "FINNIFTY": 5.0,
        "MIDCPNIFTY": 10.0,
        "SENSEX": 10.0,
        "BANKEX": 10.0,
    }


def test_depth_edge_must_meet_the_configured_per_index_floor():
    from app.market_data.live_box_spread_runner import _depth_edge_below_minimum

    assert _depth_edge_below_minimum(5.95, 10.0) is True
    assert _depth_edge_below_minimum(9.999, 10.0) is True
    assert _depth_edge_below_minimum(10.0, 10.0) is False
    assert _depth_edge_below_minimum(10.25, 10.0) is False
    assert _depth_edge_below_minimum(4.99, 5.0) is True
    assert _depth_edge_below_minimum(5.0, 5.0) is False


def test_box_runner_queues_index_switch_and_applies_only_after_session_handoff():
    import app.market_data.live_box_spread_runner as mod

    runner = mod.LiveBoxSpreadRunner(
        object(),
        [mod.BoxSpreadLiveTarget("NIFTY", "INDEX")],
        allowed_stock_symbols=set(),
        future_master=object(),
        auth=object(),
    )
    old_scanner = object()
    old_tracker = object()
    runner.scanner = old_scanner
    runner.tracker = old_tracker

    queued = runner.request_reconfigure([
        mod.BoxSpreadLiveTarget("SENSEX", "INDEX"),
        mod.BoxSpreadLiveTarget("BANKNIFTY", "INDEX"),
    ])

    # Queueing must not mutate the active target set or tear down its scanner.
    assert queued["status"] == "RECONFIGURE_QUEUED"
    assert [target.underlying for target in runner.targets] == ["NIFTY"]
    assert runner.scanner is old_scanner
    assert runner.tracker is old_tracker
    assert runner._reconfigure_event.is_set()

    # The supervisor applies the new targets only after _run_session has
    # returned and its old feeds have passed through their teardown path.
    assert runner._apply_pending_targets() is True
    assert [target.underlying for target in runner.targets] == ["SENSEX", "BANKNIFTY"]
    assert runner.scanner is None
    assert runner.tracker is None
    assert not runner._reconfigure_event.is_set()
    assert runner._apply_pending_targets() is False


def test_box_runner_rejects_empty_index_switch_without_disturbing_active_targets():
    import pytest
    import app.market_data.live_box_spread_runner as mod

    runner = mod.LiveBoxSpreadRunner(
        object(),
        [mod.BoxSpreadLiveTarget("NIFTY", "INDEX")],
        allowed_stock_symbols=set(),
        future_master=object(),
        auth=object(),
    )
    with pytest.raises(ValueError, match="at least one selected index"):
        runner.request_reconfigure([])
    assert [target.underlying for target in runner.targets] == ["NIFTY"]
    assert not runner._reconfigure_event.is_set()


def test_box_runner_namespaces_all_three_feed_consumers_for_staged_sessions():
    import app.market_data.live_box_spread_runner as mod

    active = mod.LiveBoxSpreadRunner(
        object(), [mod.BoxSpreadLiveTarget("NIFTY", "INDEX")],
        allowed_stock_symbols=set(), future_master=object(), auth=object(),
        consumer_namespace="box-active-17",
    )
    staged = mod.LiveBoxSpreadRunner(
        object(), [mod.BoxSpreadLiveTarget("SENSEX", "INDEX")],
        allowed_stock_symbols=set(), future_master=object(), auth=object(),
        consumer_namespace="box-staged-18",
    )

    assert active.depth_shadow.consumer == "box-active-17-live-depth-shadow"
    assert staged.depth_shadow.consumer == "box-staged-18-live-depth-shadow"
    assert active.depth_shadow.consumer != staged.depth_shadow.consumer
    assert active.consumer_namespace != staged.consumer_namespace


def test_box_runner_rejects_unsafe_consumer_namespace():
    import pytest
    import app.market_data.live_box_spread_runner as mod

    with pytest.raises(ValueError, match="consumer_namespace"):
        mod.LiveBoxSpreadRunner(
            object(), [mod.BoxSpreadLiveTarget("NIFTY", "INDEX")],
            allowed_stock_symbols=set(), future_master=object(), auth=object(),
            consumer_namespace="box staged",
        )


def test_box_runner_can_activate_and_deactivate_a_staged_pipeline():
    import app.market_data.live_box_spread_runner as mod

    runner = mod.LiveBoxSpreadRunner(
        object(), [mod.BoxSpreadLiveTarget("NIFTY", "INDEX")],
        allowed_stock_symbols=set(), future_master=object(), auth=object(),
        consumer_namespace="box-stage-activation", publish_enabled=False,
    )

    class Pipeline:
        def __init__(self):
            self.states = []

        def set_publish_enabled(self, enabled):
            self.states.append(enabled)

    pipeline = Pipeline()
    runner.pipeline = pipeline
    assert runner._publish_enabled is False
    runner.activate_publishing()
    assert runner._publish_enabled is True
    runner.deactivate_publishing()
    assert runner._publish_enabled is False
    assert pipeline.states == [True, False]


def test_staged_session_readiness_requires_all_underlyings_options_and_full_depth():
    import app.market_data.live_box_spread_runner as mod
    snapshot = {
        "underlying_feed": {"symbols": {
            "NIFTY": {"state": "LIVE"},
            "SENSEX": {"state": "LIVE"},
        }},
        "option_recorder": {
            "subscriptions": 100, "records_received": 12,
            "feed": {"active_instruments": 100, "connected_groups": [1]},
        "fresh_contracts": [
            {"underlying": "NIFTY", "expiry": "", "option_type": "CE"},
            {"underlying": "NIFTY", "expiry": "", "option_type": "CE"},
            {"underlying": "NIFTY", "expiry": "", "option_type": "PE"},
            {"underlying": "NIFTY", "expiry": "", "option_type": "PE"},
            {"underlying": "SENSEX", "expiry": "", "option_type": "CE"},
            {"underlying": "SENSEX", "expiry": "", "option_type": "CE"},
            {"underlying": "SENSEX", "expiry": "", "option_type": "PE"},
            {"underlying": "SENSEX", "expiry": "", "option_type": "PE"},
        ],
        },
        "depth_shadow": {"enabled": True, "running": True, "readiness": [
            {"underlying": "NIFTY", "state": "READY", "executable_depth_ce": 2, "executable_depth_pe": 2, "executable_depth_contracts": 4},
            {"underlying": "SENSEX", "state": "READY", "executable_depth_ce": 2, "executable_depth_pe": 2, "executable_depth_contracts": 4},
        ]},
    }
    result = mod.LiveBoxSpreadRunner.staged_session_readiness(snapshot, {"NIFTY", "SENSEX"})
    assert result["ready"] is True
    assert result["state"] == "READY"


def test_staged_session_readiness_fails_closed_for_one_stale_underlying_or_partial_depth():
    import app.market_data.live_box_spread_runner as mod
    snapshot = {
        "underlying_feed": {"symbols": {
            "NIFTY": {"state": "LIVE"},
            "SENSEX": {"state": "STALE"},
        }},
        "option_recorder": {
            "subscriptions": 100, "records_received": 12,
            "feed": {"active_instruments": 100, "connected_groups": [1]},
            "fresh_contracts": [
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "PE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "PE"},
            ],
        },
        "depth_shadow": {"enabled": True, "running": True, "readiness": [
            {"underlying": "NIFTY", "expiry": 20261106, "state": "READY", "executable_depth_ce": 2, "executable_depth_pe": 2, "executable_depth_contracts": 4},
            {"underlying": "SENSEX", "expiry": 20261106, "state": "WAITING_FOR_DEPTH", "executable_depth_ce": 1, "executable_depth_pe": 2, "executable_depth_contracts": 3},
        ]},
    }
    result = mod.LiveBoxSpreadRunner.staged_session_readiness(
        snapshot, {("NIFTY", "06NOV2026"), ("SENSEX", "06NOV2026")}
    )
    assert result["ready"] is False
    assert result["missing_underlying_symbols"] == ["SENSEX"]
    assert result["missing_depth_symbols"] == ["SENSEX"]


def test_staged_session_readiness_rejects_disconnected_option_feed():
    import app.market_data.live_box_spread_runner as mod
    snapshot = {
        "underlying_feed": {"symbols": {"NIFTY": {"state": "LIVE"}}},
        "option_recorder": {
            "subscriptions": 100, "records_received": 12,
            "feed": {"active_instruments": 100, "connected_groups": [], "disconnected_groups": [1]},
            "fresh_contracts": [
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "PE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "PE"},
            ],
        },
        "depth_shadow": {"enabled": True, "running": True, "readiness": [
            {"underlying": "NIFTY", "expiry": 20261106, "state": "READY", "executable_depth_ce": 2, "executable_depth_pe": 2, "executable_depth_contracts": 4},
        ]},
    }
    result = mod.LiveBoxSpreadRunner.staged_session_readiness(
        snapshot, {("NIFTY", "06NOV2026")}
    )
    assert result["ready"] is False
    assert result["option_recorder_ready"] is False


def test_staged_readiness_requires_every_selected_expiry_group():
    import app.market_data.live_box_spread_runner as mod

    snapshot = {
        "underlying_feed": {"symbols": {"NIFTY": {"state": "LIVE"}}},
        "option_recorder": {
            "subscriptions": 8,
            "feed": {"active_instruments": 8, "connected_groups": [1], "disconnected_groups": []},
            "fresh_contracts": [
                {"underlying": "NIFTY", "expiry": "WEEKLY", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "WEEKLY", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "WEEKLY", "option_type": "PE"},
                {"underlying": "NIFTY", "expiry": "WEEKLY", "option_type": "PE"},
                {"underlying": "NIFTY", "expiry": "MONTHLY", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "MONTHLY", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "MONTHLY", "option_type": "PE"},
                {"underlying": "NIFTY", "expiry": "MONTHLY", "option_type": "PE"},
            ],
        },
        "depth_shadow": {
            "enabled": True,
            "running": True,
            "readiness": [
                {"underlying": "NIFTY", "expiry": "WEEKLY", "state": "READY",
                 "executable_depth_ce": 2, "executable_depth_pe": 2, "executable_depth_contracts": 4},
            ],
        },
    }
    required = {("NIFTY", "WEEKLY"), ("NIFTY", "MONTHLY")}
    result = mod.LiveBoxSpreadRunner.staged_session_readiness(snapshot, required)
    assert result["ready"] is False
    assert result["missing_expiry_groups"] == [["NIFTY", "MONTHLY"]]

    snapshot["depth_shadow"]["readiness"].append(
        {"underlying": "NIFTY", "expiry": "MONTHLY", "state": "READY",
         "executable_depth_ce": 2, "executable_depth_pe": 2, "executable_depth_contracts": 4}
    )
    result = mod.LiveBoxSpreadRunner.staged_session_readiness(snapshot, required)
    assert result["ready"] is True
    assert result["missing_expiry_groups"] == []



def test_staged_readiness_normalizes_broker_expiry_formats_across_feeds():
    from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner

    snapshot = {
        "underlying_feed": {"symbols": {"NIFTY": {"state": "LIVE"}}},
        "option_recorder": {
            "subscriptions": 4,
            "feed": {"active_instruments": 4, "connected_groups": [1], "disconnected_groups": []},
            "fresh_contracts": [
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "PE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "PE"},
            ],
        },
        "depth_shadow": {
            "enabled": True,
            "running": True,
            "readiness": [{
                "underlying": "NIFTY",
                "expiry": 20261106,
                "state": "READY",
                "executable_depth_contracts": 4,
                "executable_depth_ce": 2,
                "executable_depth_pe": 2,
            }],
        },
    }
    result = LiveBoxSpreadRunner.staged_session_readiness(
        snapshot, {("NIFTY", "06NOV2026")}
    )
    assert result["ready"] is True
    assert result["missing_expiry_groups"] == []



def test_staged_readiness_allows_spot_only_index_without_option_chain():
    from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner

    snapshot = {
        "underlying_feed": {"symbols": {"FINNIFTY": {"state": "LIVE"}}},
        "option_recorder": {
            "subscriptions": 0,
            "feed": {"active_instruments": 0, "connected_groups": [], "disconnected_groups": []},
            "fresh_contracts": [],
        },
        "depth_shadow": {"enabled": False, "running": False, "readiness": []},
    }
    result = LiveBoxSpreadRunner.staged_session_readiness(
        snapshot, {("FINNIFTY", None)}
    )
    assert result["ready"] is True
    assert result["missing_underlying_symbols"] == []
    assert result["missing_depth_symbols"] == []


def test_staged_readiness_requires_depth_only_for_indices_with_selected_expiries():
    from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner

    snapshot = {
        "underlying_feed": {
            "symbols": {
                "NIFTY": {"state": "LIVE"},
                "FINNIFTY": {"state": "LIVE"},
            }
        },
        "option_recorder": {
            "subscriptions": 4,
            "feed": {"active_instruments": 4, "connected_groups": [1], "disconnected_groups": []},
            "fresh_contracts": [
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "CE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "PE"},
                {"underlying": "NIFTY", "expiry": "06NOV2026", "option_type": "PE"},
            ],
        },
        "depth_shadow": {
            "enabled": True,
            "running": True,
            "readiness": [{
                "underlying": "NIFTY",
                "expiry": 20261106,
                "state": "READY",
                "executable_depth_contracts": 4,
                "executable_depth_ce": 2,
                "executable_depth_pe": 2,
            }],
        },
    }
    result = LiveBoxSpreadRunner.staged_session_readiness(
        snapshot, {("NIFTY", "06NOV2026"), ("FINNIFTY", None)}
    )
    assert result["ready"] is True
    assert result["missing_depth_symbols"] == []
    assert result["missing_expiry_groups"] == []


def test_weekly_only_without_weekly_expiry_never_calls_selector_or_falls_back_to_monthly(monkeypatch):
    import app.market_data.live_box_spread_runner as mod

    class Master:
        _loaded = True
        instruments = [
            {
                "exch_seg": "NFO", "name": "NIFTY",
                "instrumenttype": "OPTIDX", "expiry": "26NOV2026",
                "strike": "25000",
            }
        ]
        def download(self):
            raise AssertionError("already-loaded master must not be downloaded")

    class Tracker:
        def __init__(self, *args, **kwargs):
            pass
        def atm(self, symbol, now_ns, **kwargs):
            return 25000.0

    class Feed:
        def __init__(self, *args, **kwargs):
            pass
        def run_forever(self):
            pass
        def stop(self):
            pass

    selector_calls = []
    def selector(*args, **kwargs):
        selector_calls.append(kwargs)
        raise AssertionError("weekly-only mode must not infer a monthly expiry")

    monkeypatch.setattr(mod, "LiveSyntheticAtmTracker", Tracker)
    monkeypatch.setattr(mod, "LiveSyntheticUnderlyingFeed", Feed)
    monkeypatch.setattr(mod, "concrete_strikes_from_master", lambda *a, **k: {"NIFTY": ()})
    monkeypatch.setattr(mod, "select_box_contracts", selector)

    runner = mod.LiveBoxSpreadRunner(
        object(),
        [mod.BoxSpreadLiveTarget("NIFTY", "INDEX", expiry=None, weekly_only=True)],
        allowed_stock_symbols=set(),
        future_master=Master(),
        auth=object(),
    )
    monkeypatch.setattr(mod, "sleep", lambda _seconds: runner.stop_event.set())

    runner._run_session()

    assert selector_calls == []
    assert runner._readiness["state"] == "waiting_for_contracts"
    assert runner._readiness["unavailable_symbols"] == [
        {"symbol": "NIFTY", "reason": "WEEKLY_EXPIRY_UNAVAILABLE"}
    ]
