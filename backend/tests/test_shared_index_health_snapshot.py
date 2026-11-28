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
    assert groups["NIFTY:option"]["ce_count"] == 0
    assert groups["NIFTY:option"]["pe_count"] == 0


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
            if kind == "option":
                groups[underlying + ":" + kind].update({
                    "ce_count": 31, "pe_count": 31,
                    "ce_fresh_count": 31, "pe_fresh_count": 31,
                })

    class FakeState:
        def snapshot(self):
            return {"instrument_count": 128, "fresh_count": 128, "feed_health_by_group": groups}

    class FakeLifecycle:
        def snapshot(self):
            return {
                "spot_active": True, "spot_instruments": 2,
                "futures_active": True, "futures_instruments": 2,
                "options_active": True, "options_instruments": 124,
                "shared_websocket": {"socket_health_by_group": {"3:0": {"connected": True}}},
            }

    runner.state = FakeState()
    runner.lifecycle = FakeLifecycle()

    snapshot = runner.health_snapshot()

    assert snapshot["enabled"] is True
    assert snapshot["live_data_ready"] is True
    assert snapshot["universe_coverage"]["complete"] is True
    assert snapshot["latest_spots"]["NIFTY"]["price"] == 26000.0
    assert snapshot["feed_health_by_group"]["BANKNIFTY:option"]["count"] == 62
    assert snapshot["readiness"]["core_market_data_fresh"] is True
    assert snapshot["readiness"]["option_activity_fresh"] is True
    assert snapshot["core_market_data_ready"] is True
    assert snapshot["readiness_reason"] == "READY"
    assert snapshot["option_quote_coverage"] == {
        "fresh": 124, "total": 124, "stale": 0, "complete": True, "percentage": 100.0,
    }
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




def test_runner_health_ready_when_illiquid_options_are_stale_but_both_sides_are_live() -> None:
    runner = object.__new__(SharedIndexMarketDataRunner)
    runner._lock = RLock()
    runner._running = True
    runner._started_at = "2026-11-26T09:00:00+05:30"
    runner._start_error = None
    runner._bootstrap_options_done = True
    runner._master_refresh_error = None
    runner._master_last_refresh_at = None
    runner._spot_records = {
        "NIFTY": SimpleNamespace(ltp=26000.0, timestamp_ns=time_ns()),
        "BANKNIFTY": SimpleNamespace(ltp=59000.0, timestamp_ns=time_ns()),
    }
    groups = {}
    for underlying in ("NIFTY", "BANKNIFTY"):
        groups[f"{underlying}:index"] = {"count": 1, "fresh_count": 1, "stale_count": 0}
        groups[f"{underlying}:future"] = {"count": 1, "fresh_count": 1, "stale_count": 0}
        groups[f"{underlying}:option"] = {
            "count": 62, "fresh_count": 8, "stale_count": 54,
            "ce_count": 31, "pe_count": 31,
            "ce_fresh_count": 20, "pe_fresh_count": 20,
        }

    class FakeState:
        def snapshot(self):
            return {"instrument_count": 128, "fresh_count": 20, "feed_health_by_group": groups}

    class FakeLifecycle:
        def snapshot(self):
            return {
                "spot_active": True, "spot_instruments": 2,
                "futures_active": True, "futures_instruments": 2,
                "options_active": True, "options_instruments": 124,
                "shared_websocket": {"socket_health_by_group": {"3:0": {"connected": True}}},
            }

    runner.state = FakeState()
    runner.lifecycle = FakeLifecycle()
    snapshot = runner.health_snapshot()

    assert snapshot["all_records_fresh"] is False
    assert snapshot["live_data_ready"] is True
    assert snapshot["readiness"]["core_market_data_fresh"] is True
    assert snapshot["readiness"]["option_activity_fresh"] is True
    assert snapshot["readiness"]["option_side_freshness"]["BANKNIFTY"]["CE"]["fresh"] == 20
    assert snapshot["core_market_data_ready"] is True
    assert snapshot["live_data_ready"] is True
    assert snapshot["option_quote_coverage"] == {
        "fresh": 80, "total": 124, "stale": 44, "complete": False, "percentage": 64.5,
    }


def test_runner_health_not_ready_when_one_core_future_is_stale_or_option_side_silent() -> None:
    from app.market_data.shared_index_market_data_runner import _readiness_from_groups

    groups = {}
    for underlying in ("NIFTY", "BANKNIFTY"):
        groups[f"{underlying}:index"] = {"count": 1, "fresh_count": 1}
        groups[f"{underlying}:future"] = {"count": 1, "fresh_count": 1}
        groups[f"{underlying}:option"] = {
            "count": 62, "fresh_count": 10, "ce_count": 31, "pe_count": 31,
            "ce_fresh_count": 20, "pe_fresh_count": 0,
        }
    result = _readiness_from_groups(running=True, bootstrap_done=True, universe_complete=True, feed_groups=groups)
    assert result["live_data_ready"] is False
    groups["BANKNIFTY:option"]["pe_fresh_count"] = 16
    groups["NIFTY:future"]["fresh_count"] = 0
    result = _readiness_from_groups(running=True, bootstrap_done=True, universe_complete=True, feed_groups=groups)
    assert result["live_data_ready"] is False



def test_readiness_exposes_core_ready_without_weakening_option_activity_gate() -> None:
    from app.market_data.shared_index_market_data_runner import _readiness_from_groups

    groups = {}
    for underlying in ("NIFTY", "BANKNIFTY"):
        groups[f"{underlying}:index"] = {"count": 1, "fresh_count": 1}
        groups[f"{underlying}:future"] = {"count": 1, "fresh_count": 1}
        groups[f"{underlying}:option"] = {
            "count": 62, "ce_count": 31, "pe_count": 31,
            "ce_fresh_count": 10, "pe_fresh_count": 8,
        }

    result = _readiness_from_groups(
        running=True, bootstrap_done=True, universe_complete=True, feed_groups=groups,
    )

    assert result["core_market_data_ready"] is True
    assert result["live_data_ready"] is False
    assert result["readiness_reason"] == "OPTION_ACTIVITY_BELOW_POLICY"
    assert result["option_quote_coverage"] == {
        "fresh": 36, "total": 124, "stale": 88, "complete": False, "percentage": 29.0,
    }


def test_readiness_reason_identifies_stale_core_feed_and_bootstrap_state() -> None:
    from app.market_data.shared_index_market_data_runner import _readiness_from_groups

    groups = {}
    for underlying in ("NIFTY", "BANKNIFTY"):
        groups[f"{underlying}:index"] = {"count": 1, "fresh_count": 1}
        groups[f"{underlying}:future"] = {"count": 1, "fresh_count": 1}
        groups[f"{underlying}:option"] = {
            "count": 62, "ce_count": 31, "pe_count": 31,
            "ce_fresh_count": 31, "pe_fresh_count": 31,
        }

    groups["BANKNIFTY:future"]["fresh_count"] = 0
    stale_core = _readiness_from_groups(
        running=True, bootstrap_done=True, universe_complete=True, feed_groups=groups,
    )
    assert stale_core["core_market_data_ready"] is False
    assert stale_core["readiness_reason"] == "CORE_FEED_NOT_FRESH"

    # Restore the core future after the stale-feed scenario so the bootstrap
    # assertions isolate option bootstrap state rather than reusing stale data.
    groups["BANKNIFTY:future"]["fresh_count"] = 1

    pending = _readiness_from_groups(
        running=True, bootstrap_done=False, universe_complete=True, feed_groups=groups,
    )
    assert pending["core_market_data_ready"] is True
    assert pending["live_data_ready"] is False
    assert pending["readiness_reason"] == "OPTION_BOOTSTRAP_PENDING"

    incomplete = _readiness_from_groups(
        running=True, bootstrap_done=True, universe_complete=False, feed_groups=groups,
    )
    assert incomplete["core_market_data_ready"] is True
    assert incomplete["live_data_ready"] is False
    assert incomplete["readiness_reason"] == "UNIVERSE_INCOMPLETE"

    stopped = _readiness_from_groups(
        running=False, bootstrap_done=True, universe_complete=True, feed_groups=groups,
    )
    assert stopped["live_data_ready"] is False
    assert stopped["readiness_reason"] == "RUNNER_STOPPED"
