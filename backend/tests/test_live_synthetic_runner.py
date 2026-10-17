from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_runner import LiveSyntheticRunner, SyntheticLiveTarget, select_nearest_option_expiry, select_monthly_option_expiry


def _master():
    master = InstrumentMaster()
    items = [
        {"exch_seg":"NFO","instrumenttype":"FUTIDX","token":"f1","name":"NIFTY",
         "symbol":"NIFTY29OCT26FUT","expiry":"29OCT2026","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"FUTIDX","token":"f2","name":"NIFTY",
         "symbol":"NIFTY26NOV26FUT","expiry":"26NOV2026","lotsize":"50"},
    ]
    for i, strike in enumerate(range(5, 201, 5)):
        items.extend([
            {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":f"c{strike}","name":"NIFTY",
             "symbol":f"NIFTY29OCT26{strike}CE","expiry":"29OCT2026","strike":str(strike * 100),"lotsize":"50"},
            {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":f"p{strike}","name":"NIFTY",
             "symbol":f"NIFTY29OCT26{strike}PE","expiry":"29OCT2026","strike":str(strike * 100),"lotsize":"50"},
        ])
    master.instruments = items
    master._loaded = True
    master.download = lambda force=False: master.instruments
    return master


def test_runner_skips_targets_until_monthly_expiry_is_resolved():
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, None)],
        allowed_stock_symbols=frozenset(),
        instrument_master=_master(),
        atm_provider=lambda _symbol, _timestamp: 100.0,
    )

    assert runner.build_subscriptions() == ()


def test_contract_selection_uses_india_market_date_not_host_date(monkeypatch):
    from datetime import datetime as RealDateTime
    import app.market_data.synthetic_subscriptions as subscriptions_module

    class SplitDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            # Simulate a host still on Oct 17 while India has crossed into Oct 30.
            if tz is None:
                return cls(2026, 10, 17)
            return cls(2026, 10, 30, tzinfo=tz)

    monkeypatch.setattr(subscriptions_module, "datetime", SplitDateTime)
    with __import__("pytest").raises(LookupError, match="does not match"):
        subscriptions_module.select_synthetic_contracts(
            _master(),
            underlying="NIFTY",
            instrument_class="INDEX",
            atm_strike=100.0,
            expiry="29OCT2026",
        )


def test_runner_builds_concrete_real_tokens_without_duplicates():
    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
        atm_provider=lambda _s, _t: 100.0,
    )
    subscriptions = runner.build_subscriptions()
    tokens = {item.token for item in subscriptions}
    assert {"f1", "c100", "p100", "c105", "p105", "c95", "p95"} <= tokens
    assert "f2" not in tokens


def test_runner_build_subscriptions_follow_live_atm():
    master = _master()
    current = {"value": 100.0}
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
        atm_provider=lambda _s, _t: current["value"],
    )
    first = {item.token for item in runner.build_subscriptions()}
    current["value"] = 105.0
    second = {item.token for item in runner.build_subscriptions()}
    assert "c100" in first and "p100" in first
    assert "c105" in second and "p105" in second
    assert first != second


def test_runner_builds_automatic_atm_tracker_from_real_chain():
    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", None, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
    )
    provider = runner._ensure_atm_provider()
    assert provider("NIFTY", 0) is None
    assert runner._atm_tracker is not None
    assert runner._atm_tracker.update("NIFTY", 103.0) == 105.0
    assert provider("NIFTY", 0) == 105.0
    subscriptions = runner.build_subscriptions()
    assert {item.token for item in subscriptions} >= {
        "f1", "c95", "p95", "c100", "p100", "c105", "p105"
    }


def test_runner_requires_live_atm_when_automatic_tracker_has_no_price():
    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", None, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
    )
    assert runner.build_subscriptions() == ()


def test_runner_exposes_concrete_atm_strikes_from_master():
    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
        atm_provider=lambda _s, _t: 100.0,
    )
    assert runner.concrete_atm_strikes() == {"NIFTY": tuple(float(strike) for strike in range(5, 201, 5))}


def test_runner_refresh_snapshot_uses_automatic_atm_provider(monkeypatch):
    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", None, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
    )
    provider = runner._ensure_atm_provider()
    runner._atm_tracker.update("NIFTY", 103.0)
    observed = provider("NIFTY", 0)
    assert observed == 105.0
    assert not hasattr(runner, "atm_provider")


def test_runner_refreshes_authoritative_stock_universe_before_selection():
    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("ABC", "STOCK", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        stock_universe_provider=lambda: ("ABC",),
        instrument_master=master,
    )
    assert runner.allowed_stock_symbols == frozenset()
    runner._refresh_stock_universe()
    assert runner.allowed_stock_symbols == frozenset({"ABC"})


def test_missing_atm_for_one_symbol_does_not_block_ready_symbol():
    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026"),
         SyntheticLiveTarget("MISSING", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(), instrument_master=master,
        atm_provider=lambda symbol, _ts: 100.0 if symbol == "NIFTY" else None,
    )
    runner._wait_for_live_atm(runner._ensure_atm_provider(), timeout_seconds=0.1)
    subscriptions = runner.build_subscriptions()
    assert subscriptions
    assert {item.underlying for item in subscriptions} == {"NIFTY"}


def test_expiry_refresh_replaces_expired_target_with_listed_matching_expiry():
    from datetime import date, timedelta

    master = _master()
    expected_expiry = (date.today() + timedelta(days=30)).strftime("%d%b%Y").upper()
    expired_expiry = (date.today() - timedelta(days=1)).strftime("%d%b%Y").upper()
    for item in master.instruments:
        if item.get("instrumenttype") in {"FUTIDX", "OPTIDX"}:
            item["expiry"] = expected_expiry
    runner = LiveSyntheticRunner(
        ":memory:", [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, expired_expiry)],
        allowed_stock_symbols=frozenset(), instrument_master=master,
        atm_provider=lambda _s, _t: 100.0,
    )
    assert runner._refresh_target_expiries() is True
    assert runner.targets[0].expiry == expected_expiry


def test_runner_stock_path_uses_provider_and_only_real_plus_minus_7_positions():
    master = InstrumentMaster()
    items = [
        {"exch_seg":"NFO","instrumenttype":"FUTSTK","token":"sf","name":"ABC",
         "symbol":"ABC29OCT26FUT","expiry":"29OCT2026","lotsize":"10"},
    ]
    for i, strike in enumerate(range(70, 131, 5)):
        items.extend([
            {"exch_seg":"NFO","instrumenttype":"OPTSTK","token":f"c{i}","name":"ABC",
             "symbol":f"ABC{strike}CE","expiry":"29OCT2026","strike":str(strike * 100),"lotsize":"10"},
            {"exch_seg":"NFO","instrumenttype":"OPTSTK","token":f"p{i}","name":"ABC",
             "symbol":f"ABC{strike}PE","expiry":"29OCT2026","strike":str(strike * 100),"lotsize":"10"},
        ])
    master.instruments = items
    master._loaded = True
    master.download = lambda force=False: master.instruments
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("ABC", "STOCK", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        stock_universe_provider=lambda: ("ABC",),
        instrument_master=master,
        atm_provider=lambda _s, _t: 100.0,
    )
    subscriptions = runner.build_subscriptions()
    strikes = {item.strike for item in subscriptions if item.strike is not None}
    assert strikes == {70.0, 75.0, 80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0, 125.0, 130.0}
    assert "sf" in {item.token for item in subscriptions}


def test_runner_stop_stops_auto_created_underlying_feed():
    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", None, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
    )

    class FakeFeed:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    feed = FakeFeed()
    runner._active_underlying_feed = feed
    runner.stop()

    assert runner._stop_requested.is_set()
    assert feed.stopped is True


def test_runner_reuses_alert_service_across_pipeline_refreshes():
    master = _master()
    alerts = object()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
        atm_provider=lambda _s, _t: 100.0,
        alerts=alerts,
    )

    first = runner._build_pipeline(object())
    second = runner._build_pipeline(object())

    assert first.alerts is alerts
    assert second.alerts is alerts
    assert first.alerts is second.alerts


def test_runner_retries_when_selected_expiry_has_no_concrete_strikes(monkeypatch):
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "token": "f1",
         "name": "NIFTY", "symbol": "NIFTY29OCT26FUT",
         "expiry": "29OCT2026", "lotsize": "50"},
    ]
    master._loaded = True
    master.download = lambda force=False: master.instruments
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", None, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
    )
    sleeps = []
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.sleep",
        lambda seconds: (sleeps.append(seconds), runner.stop()),
    )
    runner.run_forever()
    assert sleeps == [30.0]
    assert runner._stop_requested.is_set()


def test_select_nearest_option_expiry_uses_real_current_chain():
    master = _master()
    expiry = select_nearest_option_expiry(
        master.instruments,
        underlying="NIFTY",
        instrument_class="INDEX",
        today=__import__("datetime").date(2026, 10, 3),
    )
    assert expiry == "29OCT2026"


def test_select_nearest_option_expiry_returns_none_without_options():
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg":"NFO","instrumenttype":"FUTIDX","token":"f1","name":"NIFTY",
         "symbol":"NIFTY29OCT26FUT","expiry":"29OCT2026","lotsize":"50"},
    ]
    expiry = select_nearest_option_expiry(
        master.instruments,
        underlying="NIFTY",
        instrument_class="INDEX",
        today=__import__("datetime").date(2026, 10, 3),
    )
    assert expiry is None


def test_runner_filters_targets_to_resolvable_underlying_tokens(monkeypatch):
    from app.market_data.live_synthetic_runner import LiveSyntheticRunner, SyntheticLiveTarget
    class Feed:
        def _tokens(self): return {"NIFTY": "26000"}
    runner = LiveSyntheticRunner(":memory:", [SyntheticLiveTarget("NIFTY","INDEX","29OCT2026"), SyntheticLiveTarget("COPPER","COMMODITY","29OCT2026")], allowed_stock_symbols=frozenset())
    monkeypatch.setattr(runner, "_atm_tracker", object())
    monkeypatch.setattr("app.market_data.live_synthetic_runner.LiveSyntheticUnderlyingFeed", lambda *a, **k: Feed())
    feed = runner._ensure_underlying_feed()
    assert feed is not None
    assert [x.underlying for x in runner.targets] == ["NIFTY"]


def test_runner_exposes_supervisor_stop_event_alias():
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=_master(),
        atm_provider=lambda _s, _t: 100.0,
    )
    assert runner.stop_event is runner._stop_requested
    runner.stop()
    assert runner.stop_event.is_set()


def test_runner_retries_live_atm_timeout_without_terminating(monkeypatch):
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=_master(),
        atm_provider=lambda _s, _t: 100.0,
    )
    runner._atm_tracker = object()
    calls = {"wait": 0}

    def fake_wait(_provider):
        calls["wait"] += 1
        if calls["wait"] == 1:
            raise TimeoutError("temporary live-feed timeout")
        runner.stop()

    monkeypatch.setattr(runner, "_wait_for_live_atm", fake_wait)
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.LiveSyntheticOptionFutureRecorder.market_open",
        staticmethod(lambda now=None: True),
    )
    monkeypatch.setattr(runner._stop_requested, "wait", lambda _seconds: None)
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.sleep",
        lambda seconds: None,
    )
    class FakeFeed:
        def run_forever(self):
            return

        def stop(self):
            return

    monkeypatch.setattr(runner, "_ensure_underlying_feed", lambda: FakeFeed())

    runner.run_forever()
    assert calls["wait"] == 2


def test_runner_waits_outside_market_hours_without_starting_synthetic_feed(monkeypatch):
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", None, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=_master(),
        atm_provider=lambda _symbol, _timestamp: None,
    )
    created = []
    monkeypatch.setattr(
        runner,
        "_ensure_underlying_feed",
        lambda: created.append(True),
    )
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.LiveSyntheticOptionFutureRecorder.market_open",
        staticmethod(lambda now=None: False),
    )
    monkeypatch.setattr(runner._stop_requested, "wait", lambda _seconds: runner.stop())

    runner.run_forever()

    assert created == []
    assert runner.stop_event.is_set()


def test_monthly_index_expiry_matches_real_futures_and_skips_weekly_chain():
    from datetime import date

    items = [
        {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "FUTIDX", "expiry": "29OCT2026"},
        {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "FUTIDX", "expiry": "26NOV2026"},
        {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "22OCT2026"},
        {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "29OCT2026"},
        {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "26NOV2026"},
    ]
    selected = select_monthly_option_expiry(
        items, underlying="NIFTY", instrument_class="INDEX", today=date(2026, 10, 15)
    )
    assert selected == "29OCT2026"


def test_monthly_index_expiry_uses_bfo_for_sensex_and_bankex():
    from datetime import date

    for symbol in ("SENSEX", "BANKEX"):
        items = [
            {"exch_seg": "BFO", "name": symbol, "instrumenttype": "FUTIDX", "expiry": "29OCT2026"},
            {"exch_seg": "BFO", "name": symbol, "instrumenttype": "OPTIDX", "expiry": "22OCT2026"},
            {"exch_seg": "BFO", "name": symbol, "instrumenttype": "OPTIDX", "expiry": "29OCT2026"},
            {"exch_seg": "NFO", "name": symbol, "instrumenttype": "OPTIDX", "expiry": "29OCT2026"},
        ]
        assert select_monthly_option_expiry(
            items, underlying=symbol, instrument_class="INDEX", today=date(2026, 10, 15)
        ) == "29OCT2026"


def test_monthly_index_universe_contains_all_six_supported_indices():
    from app.market_data.nifty50_universe import NIFTY50_INDEX_SYMBOLS

    assert NIFTY50_INDEX_SYMBOLS == frozenset({
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"
    })


def test_monthly_stock_expiry_matches_real_futures_and_skips_weekly_chain():
    from datetime import date

    items = [
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "FUTSTK", "expiry": "29OCT2026"},
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "FUTSTK", "expiry": "26NOV2026"},
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "OPTSTK", "expiry": "22OCT2026"},
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "OPTSTK", "expiry": "29OCT2026"},
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "OPTSTK", "expiry": "26NOV2026"},
    ]
    selected = select_monthly_option_expiry(
        items, underlying="RELIANCE", instrument_class="STOCK", today=date(2026, 10, 15)
    )
    assert selected == "29OCT2026"


def test_monthly_stock_expiry_returns_none_when_only_weekly_options_exist():
    from datetime import date

    items = [
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "FUTSTK", "expiry": "29OCT2026"},
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "OPTSTK", "expiry": "22OCT2026"},
    ]
    assert select_monthly_option_expiry(
        items, underlying="RELIANCE", instrument_class="STOCK", today=date(2026, 10, 15)
    ) is None


def test_runner_restarts_recorder_after_market_session_ends(monkeypatch):
    from threading import Event

    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
        atm_provider=lambda _symbol, _timestamp: 100.0,
    )
    market = {"open": True}
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.LiveSyntheticOptionFutureRecorder.market_open",
        staticmethod(lambda now=None: market["open"]),
    )

    class FakeFeed:
        def run_forever(self):
            return

        def stop(self):
            return

    runner.underlying_feed = FakeFeed()
    recorder_calls = []

    class FakeRecorder:
        def __init__(self, *args, **kwargs):
            self.stop_event = Event()
            self.calls = len(recorder_calls) + 1
            recorder_calls.append(self)

        def run_forever(self):
            if len(recorder_calls) == 1:
                market["open"] = False
            else:
                runner.stop()

        def stop(self):
            self.stop_event.set()

    FakeRecorder.market_open = staticmethod(lambda now=None: market["open"])
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.LiveSyntheticOptionFutureRecorder",
        FakeRecorder,
    )

    def wait_for_next_session(_seconds):
        market["open"] = True

    monkeypatch.setattr(runner._stop_requested, "wait", wait_for_next_session)

    runner.run_forever()

    assert len(recorder_calls) == 2
    assert runner.stop_event.is_set()


def test_runner_snapshot_does_not_claim_running_before_run_forever():
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=_master(),
        atm_provider=lambda _s, _t: 100.0,
    )
    snapshot = runner.snapshot()
    assert snapshot["running"] is False
    assert snapshot["scanner_initialized"] is False


def test_runner_joins_underlying_feed_thread_during_shutdown(monkeypatch):
    from threading import Event, current_thread

    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=_master(),
        atm_provider=lambda _symbol, _timestamp: 100.0,
    )
    feed_stopped = Event()
    feed_started = Event()
    feed_thread_ref = {}

    class FakeFeed:
        def run_forever(self):
            feed_thread_ref["thread"] = current_thread()
            feed_started.set()
            feed_stopped.wait(2.0)

        def stop(self):
            feed_stopped.set()

    runner.underlying_feed = FakeFeed()
    monkeypatch.setattr(runner, "_refresh_target_expiries", lambda: False)
    monkeypatch.setattr(runner, "_wait_for_live_atm", lambda _provider: feed_started.wait(1.0))
    monkeypatch.setattr(runner, "build_subscriptions", lambda: [object()])
    observed_payloads = []
    monkeypatch.setattr(
        runner,
        "_build_pipeline",
        lambda _scanner: type(
            "Pipeline",
            (),
            {"observe": lambda self, payload: observed_payloads.append(payload)},
        )(),
    )
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.LiveSyntheticOptionFutureRecorder.market_open",
        staticmethod(lambda now=None: True),
    )

    class FakeRecorder:
        def __init__(self, *args, **kwargs):
            self.stop_event = Event()

        def run_forever(self):
            runner.stop()

        def stop(self):
            self.stop_event.set()

    FakeRecorder.market_open = staticmethod(lambda now=None: True)
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.LiveSyntheticOptionFutureRecorder",
        FakeRecorder,
    )

    runner.run_forever()

    assert feed_thread_ref["thread"].is_alive() is False
    assert runner._active_underlying_feed is None


def test_late_atm_callback_cannot_stop_replacement_recorder(monkeypatch):
    from threading import Event

    atm = {"value": 100.0}
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=_master(),
        atm_provider=lambda _symbol, _timestamp: atm["value"],
    )
    runner.underlying_feed = type(
        "FakeFeed", (), {"run_forever": lambda self: None, "stop": lambda self: None}
    )()
    monkeypatch.setattr(runner, "_refresh_target_expiries", lambda: False)
    monkeypatch.setattr(runner, "_wait_for_live_atm", lambda _provider: None)
    monkeypatch.setattr(runner, "build_subscriptions", lambda: [object()])
    observed_payloads = []
    monkeypatch.setattr(
        runner,
        "_build_pipeline",
        lambda _scanner: type(
            "Pipeline",
            (),
            {"observe": lambda self, payload: observed_payloads.append(payload)},
        )(),
    )
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.LiveSyntheticOptionFutureRecorder.market_open",
        staticmethod(lambda now=None: True),
    )

    class FakeRecorder:
        instances = []

        def __init__(self, *args, **kwargs):
            self.stop_event = Event()
            self.on_observation = kwargs["on_observation"]
            self.stop_calls = 0
            type(self).instances.append(self)

        def run_forever(self):
            if len(type(self).instances) == 1:
                atm["value"] = 105.0
                self.on_observation({"underlying": "NIFTY"})
                assert self.stop_event.is_set()
            else:
                type(self).instances[0].on_observation({"underlying": "NIFTY"})
                self.stale_callback_left_replacement_running = not self.stop_event.is_set()
                runner.stop()

        def stop(self):
            self.stop_calls += 1
            self.stop_event.set()

    FakeRecorder.market_open = staticmethod(lambda now=None: True)
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.LiveSyntheticOptionFutureRecorder",
        FakeRecorder,
    )

    runner.run_forever()

    assert len(FakeRecorder.instances) == 2
    assert FakeRecorder.instances[1].stale_callback_left_replacement_running is True
    assert observed_payloads == [{"underlying": "NIFTY"}]

def test_runner_waits_for_slow_underlying_feed_exit_before_clearing_reference(monkeypatch):
    from threading import Event, Thread, current_thread
    from time import monotonic

    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "29OCT2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=_master(),
        atm_provider=lambda _symbol, _timestamp: 100.0,
    )
    release_feed = Event()
    feed_started = Event()
    feed_thread_ref = {}
    release_started = {"value": False}

    class SlowFeed:
        def run_forever(self):
            feed_thread_ref["thread"] = current_thread()
            feed_started.set()
            release_feed.wait(5.0)

        def stop(self):
            if not release_started["value"]:
                release_started["value"] = True
                def delayed_release():
                    import time
                    time.sleep(2.2)
                    release_feed.set()
                Thread(target=delayed_release, daemon=True).start()

    runner.underlying_feed = SlowFeed()
    monkeypatch.setattr(runner, "_refresh_target_expiries", lambda: False)
    monkeypatch.setattr(runner, "_wait_for_live_atm", lambda _provider: feed_started.wait(1.0))
    monkeypatch.setattr(runner, "build_subscriptions", lambda: [object()])
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.LiveSyntheticOptionFutureRecorder.market_open",
        staticmethod(lambda now=None: True),
    )

    class FakeRecorder:
        def __init__(self, *args, **kwargs):
            self.stop_event = Event()

        def run_forever(self):
            runner.stop()

        def stop(self):
            self.stop_event.set()

    FakeRecorder.market_open = staticmethod(lambda now=None: True)
    monkeypatch.setattr(
        "app.market_data.live_synthetic_runner.LiveSyntheticOptionFutureRecorder",
        FakeRecorder,
    )

    started_at = monotonic()
    runner.run_forever()
    elapsed = monotonic() - started_at

    assert elapsed >= 2.0
    assert feed_thread_ref["thread"].is_alive() is False
    assert runner._active_underlying_feed is None



def test_same_second_out_of_order_tick_does_not_replace_newer_quote():
    from threading import Lock
    from types import SimpleNamespace
    from app.market_data.live_synthetic_stream import LiveSyntheticOptionFutureRecorder

    def record(timestamp_ns):
        return SimpleNamespace(
            timestamp_ns=timestamp_ns,
            symbol="NIFTY",
            instrument=SimpleNamespace(exchange="NFO", token="123"),
            as_dict=lambda: {
                "bid": 100.0,
                "ask": 101.0,
                "underlying": "NIFTY",
            },
        )

    recorder = LiveSyntheticOptionFutureRecorder.__new__(LiveSyntheticOptionFutureRecorder)
    recorder._lock = Lock()
    recorder._latest = {}
    recorder._accept_records = True
    recorder._metadata = {}
    recorder._records_received = 0
    recorder._observations_emitted = 0
    recorder._missing_executable_quote = 0
    recorder._ingestor = None
    emitted = []
    recorder.on_observation = emitted.append

    recorder._on_record(record(1_000_000_100))
    recorder._on_record(record(1_000_000_300))
    recorder._on_record(record(1_000_000_200))  # Late tick in the same second.
    recorder._on_record(record(2_000_000_100))  # Completes the previous bucket.

    assert len(emitted) == 1
    assert emitted[0]["source_timestamp_ns"] == 1_000_000_300



def test_synthetic_recorder_rejects_callback_after_session_closes():
    from threading import Lock
    from types import SimpleNamespace
    from app.market_data.live_synthetic_stream import LiveSyntheticOptionFutureRecorder

    recorder = LiveSyntheticOptionFutureRecorder.__new__(LiveSyntheticOptionFutureRecorder)
    recorder._lock = Lock()
    recorder._latest = {}
    recorder._accept_records = False
    recorder._records_received = 0
    record = SimpleNamespace(
        timestamp_ns=1_000_000_100,
        instrument=SimpleNamespace(exchange="NFO", token="123"),
    )

    recorder._on_record(record)

    assert recorder._latest == {}
    assert recorder._records_received == 0
