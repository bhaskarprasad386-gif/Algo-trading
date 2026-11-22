from datetime import date
from app.market_data.shared_index_market_data_runner import (
    SharedIndexMarketDataRunner, choose_locked_expiry,
)
from app.market_data.contracts import MarketDataRecord, InstrumentKey, InstrumentType


class FakeMaster:
    def __init__(self, rows):
        self.rows = rows
    def download(self):
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
        "NIFTY": ["26NOV2026", "03DEC2026", "10DEC2026"],
        "BANKNIFTY": ["24DEC2026", "31DEC2026", "07JAN2027"],
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
    return rows


def test_nifty_chooses_nearest_weekly_and_banknifty_next_month_last_listed():
    rows = _rows()
    assert choose_locked_expiry(rows, "NIFTY", today=date(2026, 11, 22)) == "2026-11-26"
    assert choose_locked_expiry(rows, "BANKNIFTY", today=date(2026, 11, 22)) == "2026-12-24"


def test_live_runner_bootstraps_spot_and_48_options_after_first_spot_tick():
    rows = _rows()
    manager = RunnerManager()
    runner = SharedIndexMarketDataRunner(instrument_master=FakeMaster(rows), manager=manager)
    status = runner.start()
    assert status["enabled"] is True
    assert len(manager.registry.active_keys()) == 2
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
    assert len(manager.registry.active_keys()) == 50
    assert runner.snapshot()["lifecycle"]["options_active"] is True
    assert runner.snapshot()["state"]["fresh_count"] == 2
    runner.stop()
    assert manager.registry.active_keys() == ()
