from __future__ import annotations

from threading import RLock
from time import time_ns
from types import SimpleNamespace

from app.market_data.shared_index_market_data_state import SharedIndexMarketDataState
from app.market_data.shared_index_market_data_runner import SharedIndexMarketDataRunner


class _SnapshotRecord:
    def __init__(self, underlying: str, instrument_type: str, timestamp_ns: int):
        self.underlying = underlying
        self.instrument_type = instrument_type
        self.timestamp_ns = timestamp_ns

    def as_dict(self):
        return {"underlying": self.underlying, "instrument_type": self.instrument_type}


def test_state_snapshot_groups_freshness_by_index_and_instrument_family() -> None:
    now = time_ns()
    state = SharedIndexMarketDataState(max_age_seconds=5)
    state._latest = {
        "nifty-spot": _SnapshotRecord("NIFTY", "index", now),
        "nifty-option": _SnapshotRecord("NIFTY", "option", now - 6_000_000_000),
        "bank-future": _SnapshotRecord("BANKNIFTY", "future", now),
    }

    snapshot = state.snapshot()
    groups = snapshot["feed_health_by_group"]

    assert groups["NIFTY:index"]["count"] == 1
    assert groups["NIFTY:index"]["fresh_count"] == 1
    assert groups["NIFTY:option"]["count"] == 1
    assert groups["NIFTY:option"]["stale_count"] == 1
    assert groups["BANKNIFTY:future"]["fresh_count"] == 1
    assert snapshot["instrument_count"] == 3
    assert snapshot["fresh_count"] == 2


def test_runner_health_snapshot_is_lightweight_and_reports_ready_universe() -> None:
    runner = object.__new__(SharedIndexMarketDataRunner)
    runner._lock = RLock()
    runner._running = True
    runner._started_at = "2026-11-26T09:00:00+05:30"
    runner._start_error = None
    runner._bootstrap_options_done = True
    runner._master_refresh_error = None
    runner._master_last_refresh_at = "2026-11-26T09:00:00+05:30"
    runner._master_refresh_inflight = False
    runner._master_refresh_interval_seconds = 3600
    runner._spot_records = {
        "NIFTY": SimpleNamespace(ltp=26000.0, timestamp_ns=time_ns()),
        "BANKNIFTY": SimpleNamespace(ltp=59000.0, timestamp_ns=time_ns()),
    }

    groups = {}
    for underlying, option_count in (("NIFTY", 62), ("BANKNIFTY", 62)):
        for kind, count in (("index", 1), ("future", 1), ("option", option_count)):
            groups[underlying + ":" + kind] = {"count": count, "fresh_count": count, "stale_count": 0, "max_age_seconds": 0.1}

    class FakeState:
        def snapshot(self):
            return {"instrument_count": 128, "fresh_count": 128, "feed_health_by_group": groups}

    class FakeLifecycle:
        def snapshot(self):
            return {"shared_websocket": {"socket_health_by_group": {"3:0": {"connected": True}}}}

    runner.state = FakeState()
    runner.lifecycle = FakeLifecycle()

    snapshot = runner.health_snapshot()

    assert snapshot["enabled"] is True
    assert snapshot["live_data_ready"] is True
    assert snapshot["universe_coverage"]["complete"] is True
    assert snapshot["latest_spots"]["NIFTY"]["price"] == 26000.0
    assert snapshot["feed_health_by_group"]["BANKNIFTY:option"]["count"] == 62
    assert "records" not in snapshot
    assert "option_flow_aggregates" not in snapshot

def test_runner_instrument_rows_snapshot_is_copy_only_and_does_not_trigger_download() -> None:
    runner = object.__new__(SharedIndexMarketDataRunner)
    runner._lock = RLock()
    runner._rows = ({"symbol": "NIFTY", "token": "99926000"},)

    snapshot = runner.instrument_rows_snapshot()
    assert snapshot == ({"symbol": "NIFTY", "token": "99926000"},)
    assert snapshot[0] is not runner._rows[0]

    snapshot[0]["symbol"] = "MUTATED"
    assert runner._rows[0]["symbol"] == "NIFTY"


def test_runner_health_separates_core_feed_from_quiet_option_quote_freshness() -> None:
    runner = object.__new__(SharedIndexMarketDataRunner)
    runner._lock = RLock()
    runner._running = True
    runner._started_at = "2026-11-27T09:00:00+05:30"
    runner._start_error = None
    runner._bootstrap_options_done = True
    runner._master_refresh_error = None
    runner._master_last_refresh_at = "2026-11-27T09:00:00+05:30"
    runner._master_refresh_inflight = False
    runner._master_refresh_interval_seconds = 3600
    runner._spot_records = {
        "NIFTY": SimpleNamespace(ltp=26000.0, timestamp_ns=time_ns()),
        "BANKNIFTY": SimpleNamespace(ltp=59000.0, timestamp_ns=time_ns()),
    }

    groups = {}
    for underlying in ("NIFTY", "BANKNIFTY"):
        for kind, count, fresh in (
            ("index", 1, 1), ("future", 1, 1), ("option", 62, 56),
        ):
            groups[underlying + ":" + kind] = {
                "count": count, "fresh_count": fresh,
                "stale_count": count - fresh, "max_age_seconds": 8.0,
            }

    class FakeState:
        def snapshot(self):
            return {
                "instrument_count": 128, "fresh_count": 116,
                "feed_health_by_group": groups,
            }

    class FakeLifecycle:
        def snapshot(self):
            return {"shared_websocket": {"socket_health_by_group": {"3:0": {"connected": True}}}}

    runner.state = FakeState()
    runner.lifecycle = FakeLifecycle()

    snapshot = runner.health_snapshot()

    assert snapshot["universe_coverage"]["complete"] is True
    assert snapshot["core_market_data_ready"] is True
    assert snapshot["option_quote_coverage"] == {
        "fresh": 112, "total": 124, "stale": 12, "complete": False,
    }
    assert snapshot["live_data_ready"] is False
    assert snapshot["readiness_reason"] == "OPTION_QUOTES_PARTIAL"

