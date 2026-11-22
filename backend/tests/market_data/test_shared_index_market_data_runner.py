from datetime import date
from app.market_data.shared_index_market_data_runner import (
    SharedIndexMarketDataRunner, choose_locked_expiry,
)
from app.market_data.contracts import MarketDataRecord, InstrumentKey, InstrumentType, OptionType


class FakeMaster:
    def __init__(self, rows):
        self.rows = rows
    def download(self, *, force=False):
        return self.rows
    def resolve_index_instrument(self, symbol, exchange="NSE"):
        return {"token": "99001" if symbol == "NIFTY" else "99002", "symbol": symbol}


class RunnerManager:
    def __init__(self):
        from app.market_data.registry import InstrumentRegistry
        self.registry = InstrumentRegistry()
        self.callbacks = {}
    def register_normalized_callback(self, consumer, callback):
        self.callbacks[consumer] = callback
    def subscribe(self, consumer, keys, mode=1):
        for key in keys:
            self.registry.subscribe(consumer, key, mode)
    def unsubscribe(self, consumer, keys=None):
        for key in list(keys or self.registry.active_keys()):
            self.registry.unsubscribe(consumer, key)
    def clear_consumer(self, consumer):
        self.registry.clear_consumer(consumer)
        self.callbacks.pop(consumer, None)
    def snapshot(self):
        return {"active_subscriptions": len(self.registry.subscriptions())}


def _rows():
    expiries = {
        "NIFTY": ["19NOV2026", "26NOV2026", "03DEC2026", "10DEC2026"],
        "BANKNIFTY": ["24DEC2026", "27JAN2027", "24FEB2027"],
    }
    rows = []
    token = 1000
    for symbol, dates in expiries.items():
        for expiry in dates:
            base = 22000 if symbol == "NIFTY" else 48000
            step = 50
            for i in range(31):
                strike = base + (i - 15) * step
                for side in ("CE", "PE"):
                    rows.append({
                        "exch_seg": "NFO", "name": symbol, "instrumenttype": "OPTIDX",
                        "expiry": expiry, "strike": strike * 100,
                        "symbol": f"{symbol}{expiry}{strike}{side}", "token": str(token),
                        "lotsize": "25",
                    })
                    token += 1
    for symbol, expiries in {"NIFTY": ["26NOV2026", "24DEC2026"], "BANKNIFTY": ["24DEC2026", "27JAN2027"]}.items():
        for expiry in expiries:
            rows.append({
                "exch_seg": "NFO", "name": symbol, "instrumenttype": "FUTIDX",
                "expiry": expiry, "symbol": f"{symbol}{expiry}FUT", "token": str(token),
                "lotsize": "25",
            })
            token += 1
    return rows


def test_nifty_chooses_nearest_weekly_and_banknifty_next_month_last_listed():
    rows = _rows()
    assert choose_locked_expiry(rows, "NIFTY", today=date(2026, 11, 20)) == "2026-12-03"
    assert choose_locked_expiry(rows, "BANKNIFTY", today=date(2026, 11, 22)) == "2026-12-24"
    assert SharedIndexMarketDataRunner.choose_nearest_future(rows, "NIFTY", today=date(2026, 11, 22))["expiry"] == "2026-11-26"
    assert SharedIndexMarketDataRunner.choose_nearest_future(rows, "BANKNIFTY", today=date(2026, 11, 22))["expiry"] == "2026-12-24"


def test_partial_start_failure_leaves_runner_stopped_and_cleans_subscriptions():
    # Instrument rows omit futures; spot registration occurs first and then fails.
    rows = [row for row in _rows() if row["instrumenttype"] != "FUTIDX"]
    manager = RunnerManager()
    runner = SharedIndexMarketDataRunner(instrument_master=FakeMaster(rows), manager=manager)
    status = runner.start()
    assert status["enabled"] is False
    assert status["started_at"] is None
    assert status["last_error"] and "no active NFO FUTIDX contract" in status["last_error"]
    assert manager.registry.active_keys() == ()
    assert runner.snapshot()["lifecycle"]["spot_active"] is False
    assert runner.snapshot()["lifecycle"]["futures_active"] is False


def test_future_rollover_rotates_to_new_listed_expiry_and_updates_state():
    rows = _rows()
    manager = RunnerManager()
    runner = SharedIndexMarketDataRunner(instrument_master=FakeMaster(rows), manager=manager)
    runner.start(rows)
    runner._futures_expiries = {"NIFTY": "2026-11-20", "BANKNIFTY": "2026-12-24"}
    rolled = runner._check_futures_rollover()
    assert rolled == ("NIFTY",)
    assert runner.snapshot()["futures_expiries"]["NIFTY"] == "2026-11-26"
    active_futures = [manager.registry.get(key) for key in manager.registry.active_keys()
                       if manager.registry.get(key).instrument_type == "future"]
    assert len(active_futures) == 2
    assert {d.expiry for d in active_futures} == {"2026-11-26", "2026-12-24"}
    assert runner.snapshot()["state"]["instrument_count"] == 0
    runner.stop()


def test_live_runner_bootstraps_spot_and_48_options_after_first_spot_tick():
    rows = _rows()
    manager = RunnerManager()
    runner = SharedIndexMarketDataRunner(instrument_master=FakeMaster(rows), manager=manager)
    status = runner.start()
    assert status["enabled"] is True
    assert len(manager.registry.active_keys()) == 4
    spot_consumer = runner.lifecycle.SPOT_CONSUMER
    manager.callbacks[spot_consumer](MarketDataRecord(
        instrument=InstrumentKey("NSE", "NSE", "99001"), symbol="NIFTY",
        instrument_type=InstrumentType.INDEX, timestamp_ns=__import__("time").time_ns(),
        ltp=22000.0, underlying="NIFTY",
    ))
    manager.callbacks[spot_consumer](MarketDataRecord(
        instrument=InstrumentKey("NSE", "NSE", "99002"), symbol="BANKNIFTY",
        instrument_type=InstrumentType.INDEX, timestamp_ns=__import__("time").time_ns(),
        ltp=48000.0, underlying="BANKNIFTY",
    ))
    assert len(manager.registry.active_keys()) == 52
    assert runner.snapshot()["lifecycle"]["options_active"] is True
    assert runner.snapshot()["lifecycle"]["futures_active"] is True
    assert runner.snapshot()["futures_expiries"] == {"NIFTY": "2026-11-26", "BANKNIFTY": "2026-12-24"}
    option_descriptors = [manager.registry.get(key) for key in manager.registry.active_keys()
                         if manager.registry.get(key).instrument_type == "option"]
    nifty_strikes = sorted({d.strike for d in option_descriptors if d.underlying == "NIFTY"})
    bank_strikes = sorted({d.strike for d in option_descriptors if d.underlying == "BANKNIFTY"})
    assert len(nifty_strikes) == 13 and nifty_strikes[6] == 22000
    assert len(bank_strikes) == 11 and bank_strikes[5] == 48000
    assert runner.snapshot()["state"]["fresh_count"] == 2
    assert runner.snapshot()["live_data_ready"] is False
    futures_consumer = runner.lifecycle.FUTURES_CONSUMER
    for key in manager.registry.active_keys():
        descriptor = manager.registry.get(key)
        if descriptor.instrument_type != "future":
            continue
        manager.callbacks[futures_consumer](MarketDataRecord(
            instrument=key, symbol=descriptor.symbol, instrument_type=InstrumentType.FUTURE,
            timestamp_ns=__import__("time").time_ns(), ltp=22000.0, bid=21999.5, ask=22000.5,
            bid_qty=10, ask_qty=12, volume=1000, oi=8888,
            underlying=descriptor.underlying, expiry=descriptor.expiry, lot_size=descriptor.lot_size,
        ))
    option_consumer = runner.lifecycle.OPTIONS_CONSUMER
    for key in manager.registry.active_keys():
        descriptor = manager.registry.get(key)
        if descriptor.instrument_type != "option":
            continue
        manager.callbacks[option_consumer](MarketDataRecord(
            instrument=key, symbol=descriptor.symbol, instrument_type=InstrumentType.OPTION,
            timestamp_ns=__import__("time").time_ns(), ltp=100.0, bid=99.5, ask=100.5,
            bid_qty=10, ask_qty=12, volume=1000, oi=500,
            underlying=descriptor.underlying, expiry=descriptor.expiry,
            strike=descriptor.strike, option_type=OptionType(descriptor.option_type),
            lot_size=descriptor.lot_size,
        ))
    snap = runner.snapshot()
    assert snap["state"]["instrument_count"] == 52
    assert snap["live_data_ready"] is True
    assert len(snap["records"]) == 52
    assert all(row["feed_age_seconds"] >= 0 and row["fresh"] for row in snap["records"])
    futures_rows = [row for row in snap["records"] if row["instrument_type"] == "future"]
    assert len(futures_rows) == 2
    assert all(row["oi"] == 8888 and row["volume"] == 1000 for row in futures_rows)
    assert {row["expiry"] for row in futures_rows} == {"2026-11-26", "2026-12-24"}
    option_rows = [row for row in snap["records"] if row["instrument_type"] == "option"]
    assert len(option_rows) == 48
    assert all(row["volume"] == 1000 and row["oi"] == 500 for row in option_rows)
    assert all("volume_delta_1s" in row and "oi_delta_1s" in row for row in option_rows)
    assert snap["derived_iv_model"]["volume_source"] == "broker_day_volume"
    assert snap["derived_iv_model"]["oi_source"] == "broker_open_interest"
    runner.stop()
    assert manager.registry.active_keys() == ()
    assert runner.snapshot()["state"]["instrument_count"] == 0
    assert runner.snapshot()["live_data_ready"] is False


def test_instrument_master_refresh_publishes_complete_snapshot_without_tick_blocking():
    from threading import Event
    class RefreshMaster(FakeMaster):
        def __init__(self, rows):
            super().__init__(rows)
            self.refresh_done = Event()
        def download(self, *, force=False):
            if force:
                self.rows = self.rows + [{
                    "exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "FUTIDX",
                    "expiry": "31DEC2026", "symbol": "NIFTY31DECFUT", "token": "999999",
                    "lotsize": "25",
                }]
                self.refresh_done.set()
            return self.rows
    rows = _rows()
    master = RefreshMaster(rows)
    runner = SharedIndexMarketDataRunner(instrument_master=master, manager=RunnerManager())
    runner.start(rows)
    runner._master_refresh_interval_seconds = 0
    assert runner._schedule_master_refresh() is True
    assert master.refresh_done.wait(timeout=2)
    # Wait for worker to publish its snapshot.
    import time
    deadline = time.monotonic() + 2
    while runner._master_refresh_inflight and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(runner._rows) == len(rows) + 1
    assert runner._master_refresh_error is None
    runner.stop()


def test_failed_instrument_master_refresh_preserves_last_good_rows():
    from threading import Event
    class FailingMaster(FakeMaster):
        def __init__(self, rows):
            super().__init__(rows)
            self.done = Event()
        def download(self, *, force=False):
            if force:
                self.done.set()
                raise RuntimeError("network unavailable")
            return self.rows
    rows = _rows()
    master = FailingMaster(rows)
    runner = SharedIndexMarketDataRunner(instrument_master=master, manager=RunnerManager())
    runner.start(rows)
    original = runner._rows
    runner._master_refresh_interval_seconds = 0
    assert runner._schedule_master_refresh() is True
    assert master.done.wait(timeout=2)
    import time
    deadline = time.monotonic() + 2
    while runner._master_refresh_inflight and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner._rows is original
    assert "network unavailable" in runner._master_refresh_error
    runner.stop()


def test_refresh_started_before_off_cannot_publish_after_off():
    from threading import Event
    import time
    class BlockingMaster(FakeMaster):
        def __init__(self, rows):
            super().__init__(rows)
            self.started = Event()
            self.release = Event()
        def download(self, *, force=False):
            if force:
                self.started.set()
                assert self.release.wait(timeout=2)
                return self.rows + [{
                    "exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "FUTIDX",
                    "expiry": "31DEC2026", "symbol": "NIFTY31DECFUT", "token": "999998",
                    "lotsize": "25",
                }]
            return self.rows
    rows = _rows()
    master = BlockingMaster(rows)
    runner = SharedIndexMarketDataRunner(instrument_master=master, manager=RunnerManager())
    runner.start(rows)
    original_rows = runner._rows
    runner._master_refresh_interval_seconds = 0
    assert runner._schedule_master_refresh()
    assert master.started.wait(timeout=2)
    runner.stop()
    master.release.set()
    deadline = time.monotonic() + 2
    while runner._master_refresh_inflight and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner._running is False
    assert runner._rows is original_rows
    assert runner.snapshot()["state"]["instrument_count"] == 0


def test_external_pending_rotation_flush_syncs_state_and_retired_cache():
    from app.market_data.shared_cache import get_shared_market_data_cache
    import time

    rows = _rows()
    runner = SharedIndexMarketDataRunner(instrument_master=FakeMaster(rows), manager=RunnerManager())
    runner.start(rows)
    old_key = InstrumentKey("NFO", "NFO", "old-contract")
    new_key = InstrumentKey("NFO", "NFO", "new-contract")
    runner.state.retain_instruments([old_key])
    record = MarketDataRecord(
        instrument=old_key, symbol="OLD", instrument_type=InstrumentType.FUTURE,
        timestamp_ns=time.time_ns(), ltp=100.0, underlying="NIFTY",
        expiry="2026-11-26",
    )
    assert runner.state.on_record(record)
    assert get_shared_market_data_cache().get(old_key.value) is not None

    # Simulate a completed rotation from the pending-flush path (not a tick callback).
    runner.lifecycle.active_keys = lambda: (new_key,)
    runner.rotation.flush_due = lambda: ("NIFTY",)
    assert runner.flush_rotations() == ("NIFTY",)
    assert runner.state.latest(old_key.value) is None
    assert get_shared_market_data_cache().get(old_key.value) is None
    runner.stop()



def test_blocked_start_cannot_resurrect_runner_after_off_epoch():
    from threading import Event, Thread
    class BlockingMaster(FakeMaster):
        def __init__(self, rows):
            super().__init__(rows)
            self.started = Event()
            self.release = Event()
        def download(self, *, force=False):
            self.started.set()
            assert self.release.wait(timeout=3)
            return self.rows
    rows = _rows()
    master = BlockingMaster(rows)
    manager = RunnerManager()
    runner = SharedIndexMarketDataRunner(instrument_master=master, manager=manager)
    start_result = []
    thread = Thread(target=lambda: start_result.append(runner.start()), daemon=True)
    thread.start()
    assert master.started.wait(timeout=2)
    runner.stop()
    master.release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert runner._running is False
    assert manager.registry.active_keys() == ()
    assert runner.snapshot()["enabled"] is False



def test_new_on_waits_for_previous_off_subscription_teardown():
    from threading import Event, Thread
    rows = _rows()
    manager = RunnerManager()
    runner = SharedIndexMarketDataRunner(instrument_master=FakeMaster(rows), manager=manager)
    assert runner.start(rows)["enabled"] is True

    # Replace unsubscribe with a controlled pause during the old OFF teardown.
    original_unsubscribe = manager.unsubscribe
    entered, release = Event(), Event()
    def blocked_unsubscribe(consumer, keys=None):
        if consumer == runner.lifecycle.FUTURES_CONSUMER and not release.is_set():
            entered.set()
            assert release.wait(timeout=3)
        return original_unsubscribe(consumer, keys)
    manager.unsubscribe = blocked_unsubscribe

    stopped = []
    off_thread = Thread(target=lambda: stopped.append(runner.stop()), daemon=True)
    off_thread.start()
    assert entered.wait(timeout=2)

    started = []
    on_thread = Thread(target=lambda: started.append(runner.start(rows)), daemon=True)
    on_thread.start()
    release.set()
    off_thread.join(timeout=3)
    on_thread.join(timeout=3)

    assert not off_thread.is_alive() and not on_thread.is_alive()
    assert stopped and stopped[0]["enabled"] is False
    assert started and started[0]["enabled"] is True
    assert len(manager.registry.active_keys()) == 4
    assert runner.snapshot()["enabled"] is True
    runner.stop()



def test_stale_master_refresh_failure_cannot_pollute_restarted_runner_epoch():
    from threading import Event, Thread, enumerate as enumerate_threads
    import time

    class OldRefreshFailsAfterRestartMaster(FakeMaster):
        def __init__(self, rows):
            super().__init__(rows)
            self.refresh_started = Event()
            self.release_refresh = Event()
        def download(self, *, force=False):
            if force:
                self.refresh_started.set()
                assert self.release_refresh.wait(timeout=3)
                raise RuntimeError("old refresh failed")
            return self.rows

    rows = _rows()
    master = OldRefreshFailsAfterRestartMaster(rows)
    runner = SharedIndexMarketDataRunner(instrument_master=master, manager=RunnerManager())
    assert runner.start(rows)["enabled"] is True
    runner._master_refresh_interval_seconds = 0
    assert runner._schedule_master_refresh() is True
    assert master.refresh_started.wait(timeout=2)

    runner.stop()
    assert runner.start(rows)["enabled"] is True
    master.release_refresh.set()
    deadline = time.monotonic() + 2
    while any(t.name == "shared-index-master-refresh" for t in enumerate_threads()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner._running is True
    assert runner._master_refresh_error is None
    runner.stop()



def test_futures_rollover_is_serialized_with_runner_stop():
    from threading import Event, Thread
    import time

    rows = _rows()
    manager = RunnerManager()
    runner = SharedIndexMarketDataRunner(instrument_master=FakeMaster(rows), manager=manager)
    assert runner.start(rows)["enabled"] is True

    selected_entered, release_selection = Event(), Event()
    original_select = runner.choose_nearest_future
    def blocked_selection(rows, underlying, *, today=None):
        selected_entered.set()
        assert release_selection.wait(timeout=3)
        return original_select(rows, underlying, today=today)
    runner.choose_nearest_future = blocked_selection
    runner._futures_expiries = {}  # force a replacement-selection path for the race

    rollover_done, stop_done = Event(), Event()
    rollover_thread = Thread(target=lambda: (runner._check_futures_rollover(), rollover_done.set()), daemon=True)
    rollover_thread.start()
    assert selected_entered.wait(timeout=2)
    stop_thread = Thread(target=lambda: (runner.stop(), stop_done.set()), daemon=True)
    stop_thread.start()
    time.sleep(0.05)
    assert not stop_done.is_set(), "OFF must wait for in-flight rollover to finish"
    release_selection.set()
    rollover_thread.join(timeout=3)
    stop_thread.join(timeout=3)
    assert rollover_done.is_set() and stop_done.is_set()
    assert runner.snapshot()["enabled"] is False
    assert manager.registry.active_keys() == ()



def test_stale_option_bootstrap_cannot_subscribe_after_runner_restart(monkeypatch):
    from threading import Event, Thread
    import time
    import app.market_data.shared_index_market_data_runner as runner_module

    rows = _rows()
    manager = RunnerManager()
    runner = SharedIndexMarketDataRunner(instrument_master=FakeMaster(rows), manager=manager)
    assert runner.start(rows)["enabled"] is True
    original_select = runner_module.select_locked_index_options
    old_entered, release_old = Event(), Event()
    call_count = {"n": 0}
    def delayed_old_select(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            old_entered.set()
            assert release_old.wait(timeout=3)
        return original_select(*args, **kwargs)
    monkeypatch.setattr(runner_module, "select_locked_index_options", delayed_old_select)

    def set_spots():
        now_ns = time.time_ns()
        runner._spot_records = {
            "NIFTY": MarketDataRecord(instrument=InstrumentKey("NSE", "NSE", "99001"),
                symbol="NIFTY", instrument_type=InstrumentType.INDEX, timestamp_ns=now_ns,
                ltp=22000.0, underlying="NIFTY"),
            "BANKNIFTY": MarketDataRecord(instrument=InstrumentKey("NSE", "NSE", "99002"),
                symbol="BANKNIFTY", instrument_type=InstrumentType.INDEX, timestamp_ns=now_ns,
                ltp=48000.0, underlying="BANKNIFTY"),
        }
    set_spots()
    old_thread = Thread(target=runner._bootstrap_options, daemon=True)
    old_thread.start()
    assert old_entered.wait(timeout=2)

    runner.stop()
    assert runner.start(rows)["enabled"] is True
    set_spots()
    assert runner._bootstrap_options() is True
    active_new_epoch = set(runner.lifecycle._option_keys)
    assert len(active_new_epoch) == 48

    release_old.set()
    old_thread.join(timeout=3)
    assert not old_thread.is_alive()
    assert set(runner.lifecycle._option_keys) == active_new_epoch
    runner.stop()



def test_concurrent_nifty_bank_option_rotations_are_serialized_and_atomic(monkeypatch):
    from threading import Event, Thread
    import time
    import app.market_data.shared_index_option_rotation_controller as rotation_module

    rows = _rows()
    manager = RunnerManager()
    runner = SharedIndexMarketDataRunner(instrument_master=FakeMaster(rows), manager=manager)
    assert runner.start(rows)["enabled"] is True
    spot_consumer = runner.lifecycle.SPOT_CONSUMER
    now = time.time_ns()
    manager.callbacks[spot_consumer](MarketDataRecord(
        instrument=InstrumentKey("NSE", "NSE", "99001"), symbol="NIFTY",
        instrument_type=InstrumentType.INDEX, timestamp_ns=now, ltp=22000.0,
        underlying="NIFTY"))
    manager.callbacks[spot_consumer](MarketDataRecord(
        instrument=InstrumentKey("NSE", "NSE", "99002"), symbol="BANKNIFTY",
        instrument_type=InstrumentType.INDEX, timestamp_ns=time.time_ns(), ltp=48000.0,
        underlying="BANKNIFTY"))
    assert len(runner.lifecycle._option_keys) == 48

    original_select = rotation_module.select_locked_index_options
    first_entered, release_first, second_entered = Event(), Event(), Event()
    call = {"count": 0}
    def blocking_select(*args, **kwargs):
        call["count"] += 1
        if call["count"] == 1:
            first_entered.set()
            assert release_first.wait(timeout=3)
        else:
            second_entered.set()
        return original_select(*args, **kwargs)
    monkeypatch.setattr(rotation_module, "select_locked_index_options", blocking_select)

    first_done, second_done = Event(), Event()
    first = Thread(target=lambda: (runner.rotation._rotate("NIFTY", 22075.0), first_done.set()), daemon=True)
    second = Thread(target=lambda: (runner.rotation._rotate("BANKNIFTY", 48125.0), second_done.set()), daemon=True)
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    time.sleep(0.05)
    assert not second_entered.is_set(), "second rotation must wait for the controller lock"
    assert not second_done.is_set(), "rotation cannot interleave lifecycle subscribe/remove"
    release_first.set()
    first.join(timeout=3)
    second.join(timeout=3)
    assert first_done.is_set() and second_done.is_set()
    active = tuple(runner.lifecycle._option_keys)
    descriptors = [manager.registry.get(key) for key in active]
    assert len(active) == 48
    assert len({descriptor.key for descriptor in descriptors}) == 48
    assert {descriptor.underlying for descriptor in descriptors} == {"NIFTY", "BANKNIFTY"}
    assert len([d for d in descriptors if d.underlying == "NIFTY"]) == 26
    assert len([d for d in descriptors if d.underlying == "BANKNIFTY"]) == 22
    assert set(manager.registry.active_keys()) == set(runner.lifecycle.active_keys())
    runner.stop()
