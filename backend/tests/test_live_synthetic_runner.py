from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_runner import LiveSyntheticRunner, SyntheticLiveTarget


def _master():
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg":"NFO","instrumenttype":"FUTIDX","token":"f1","name":"NIFTY",
         "symbol":"NIFTY30SEP26FUT","expiry":"30SEP2026","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":"c100","name":"NIFTY",
         "symbol":"NIFTY30SEP26100CE","expiry":"30SEP2026","strike":"10000","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":"p100","name":"NIFTY",
         "symbol":"NIFTY30SEP26100PE","expiry":"30SEP2026","strike":"10000","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":"c105","name":"NIFTY",
         "symbol":"NIFTY30SEP26105CE","expiry":"30SEP2026","strike":"10500","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":"p105","name":"NIFTY",
         "symbol":"NIFTY30SEP26105PE","expiry":"30SEP2026","strike":"10500","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":"c95","name":"NIFTY",
         "symbol":"NIFTY30SEP2695CE","expiry":"30SEP2026","strike":"9500","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":"p95","name":"NIFTY",
         "symbol":"NIFTY30SEP2695PE","expiry":"30SEP2026","strike":"9500","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":"c90","name":"NIFTY",
         "symbol":"NIFTY30SEP2690CE","expiry":"30SEP2026","strike":"9000","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":"p90","name":"NIFTY",
         "symbol":"NIFTY30SEP2690PE","expiry":"30SEP2026","strike":"9000","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":"c110","name":"NIFTY",
         "symbol":"NIFTY30SEP26110CE","expiry":"30SEP2026","strike":"11000","lotsize":"50"},
        {"exch_seg":"NFO","instrumenttype":"OPTIDX","token":"p110","name":"NIFTY",
         "symbol":"NIFTY30SEP26110PE","expiry":"30SEP2026","strike":"11000","lotsize":"50"},
    ]
    master._loaded = True
    return master


def test_runner_builds_concrete_real_tokens_without_duplicates():
    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "30SEP2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
        atm_provider=lambda _s, _t: 100.0,
    )
    subscriptions = runner.build_subscriptions()
    tokens = {item.token for item in subscriptions}
    assert {"f1", "c100", "p100", "c105", "p105", "c95", "p95"} <= tokens


def test_runner_build_subscriptions_follow_live_atm():
    master = _master()
    current = {"value": 100.0}
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "30SEP2026")],
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
        [SyntheticLiveTarget("NIFTY", "INDEX", None, "30SEP2026")],
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
        [SyntheticLiveTarget("NIFTY", "INDEX", None, "30SEP2026")],
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
        [SyntheticLiveTarget("NIFTY", "INDEX", 100.0, "30SEP2026")],
        allowed_stock_symbols=frozenset(),
        instrument_master=master,
        atm_provider=lambda _s, _t: 100.0,
    )
    assert runner.concrete_atm_strikes() == {"NIFTY": (95.0, 100.0, 105.0)}


def test_runner_refresh_snapshot_uses_automatic_atm_provider(monkeypatch):
    master = _master()
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("NIFTY", "INDEX", None, "30SEP2026")],
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
        [SyntheticLiveTarget("ABC", "STOCK", 100.0, "30SEP2026")],
        allowed_stock_symbols=frozenset(),
        stock_universe_provider=lambda: ("ABC",),
        instrument_master=master,
    )
    assert runner.allowed_stock_symbols == frozenset()
    runner._refresh_stock_universe()
    assert runner.allowed_stock_symbols == frozenset({"ABC"})


def test_runner_stock_path_uses_provider_and_only_real_plus_minus_5_positions():
    master = InstrumentMaster()
    items = [
        {"exch_seg":"NFO","instrumenttype":"FUTSTK","token":"sf","name":"ABC",
         "symbol":"ABC30SEP26FUT","expiry":"30SEP2026","lotsize":"10"},
    ]
    for i, strike in enumerate(range(70, 131, 5)):
        items.extend([
            {"exch_seg":"NFO","instrumenttype":"OPTSTK","token":f"c{i}","name":"ABC",
             "symbol":f"ABC{strike}CE","expiry":"30SEP2026","strike":str(strike * 100),"lotsize":"10"},
            {"exch_seg":"NFO","instrumenttype":"OPTSTK","token":f"p{i}","name":"ABC",
             "symbol":f"ABC{strike}PE","expiry":"30SEP2026","strike":str(strike * 100),"lotsize":"10"},
        ])
    master.instruments = items
    master._loaded = True
    runner = LiveSyntheticRunner(
        ":memory:",
        [SyntheticLiveTarget("ABC", "STOCK", 100.0, "30SEP2026")],
        allowed_stock_symbols=frozenset(),
        stock_universe_provider=lambda: ("ABC",),
        instrument_master=master,
        atm_provider=lambda _s, _t: 100.0,
    )
    subscriptions = runner.build_subscriptions()
    strikes = {item.strike for item in subscriptions if item.strike is not None}
    assert strikes == {75.0, 80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0, 125.0}
    assert "sf" in {item.token for item in subscriptions}
