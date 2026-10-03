from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_runner import LiveSyntheticRunner, SyntheticLiveTarget, select_nearest_option_expiry


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
    assert {"f1", "f2", "c100", "p100", "c105", "p105", "c95", "p95"} <= tokens


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
    try:
        runner.build_subscriptions()
    except LookupError as exc:
        assert "live ATM price" in str(exc)
    else:
        raise AssertionError("expected LookupError")


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
    # Regression guard: automatic mode must not access the removed legacy attribute.
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
    # Keep this regression fixture authoritative and offline; the test must
    # never replace its synthetic ABC contracts with the live Angel master.
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
