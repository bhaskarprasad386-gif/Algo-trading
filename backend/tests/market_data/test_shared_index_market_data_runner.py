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
\n


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
