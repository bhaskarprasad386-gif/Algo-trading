from threading import Event, Thread
from time import sleep

import pytest

from app.market_data.dashboard_snapshot_cache import BoundedSnapshotCache


def test_snapshot_cache_serves_fresh_copy_without_recomputing():
    cache = BoundedSnapshotCache(ttl_seconds=10, max_stale_seconds=20)
    calls = []
    def factory():
        calls.append(1)
        return {"value": 7}
    first = cache.get(factory)
    first["value"] = 99
    second = cache.get(factory)
    assert second["value"] == 7
    assert second["_snapshot_cache"]["stale"] is False
    assert len(calls) == 1


def test_expired_snapshot_is_served_while_one_background_refresh_runs(monkeypatch):
    import app.market_data.dashboard_snapshot_cache as module
    clock = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    cache = BoundedSnapshotCache(ttl_seconds=1, max_stale_seconds=30)
    assert cache.get(lambda: {"value": "old"})["value"] == "old"
    clock[0] = 102.0
    started, release = Event(), Event()
    calls = []
    def slow_factory():
        calls.append(1)
        started.set()
        assert release.wait(2)
        return {"value": "new"}
    stale = cache.get(slow_factory)
    assert stale["value"] == "old"
    assert stale["_snapshot_cache"]["stale"] is True
    assert started.wait(1)
    # A second request must not launch a second refresh.
    assert cache.get(slow_factory)["value"] == "old"
    assert len(calls) == 1
    release.set()
    for _ in range(100):
        if not cache._refreshing:
            break
        sleep(0.01)
    assert cache.get(slow_factory)["value"] == "new"
    assert len(calls) == 1


def test_refresh_failure_keeps_last_good_snapshot(monkeypatch):
    import app.market_data.dashboard_snapshot_cache as module
    clock = [10.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    cache = BoundedSnapshotCache(ttl_seconds=1, max_stale_seconds=20)
    cache.get(lambda: {"value": 3})
    clock[0] = 12.0
    stale = cache.get(lambda: (_ for _ in ()).throw(RuntimeError("provider down")))
    assert stale["value"] == 3
    assert stale["_snapshot_cache"]["stale"] is True
    for _ in range(100):
        if not cache._refreshing:
            break
        sleep(0.01)
    assert cache._refresh_errors == 1


def test_cache_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        BoundedSnapshotCache(ttl_seconds=5, max_stale_seconds=4)
