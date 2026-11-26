import time
from threading import Event

import pytest

from app.market_data import persisted_summary_cache as cache_module
from app.market_data.persisted_summary_cache import BoundedPersistedSummaryCache


def _wait_for_refresh(cache, key="db", timeout=1.0):
    with cache._lock:
        event = cache._refreshing.get(key)
    if event is not None:
        assert event.wait(timeout), "background summary refresh did not finish"


def test_cache_reuses_fresh_summary_and_returns_defensive_copies(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: now[0])
    cache = BoundedPersistedSummaryCache(ttl_seconds=10, max_stale_seconds=30)
    calls = []

    def load():
        calls.append(1)
        return {"rows": {"count": 3}}

    first, first_meta = cache.get("db", load)
    first["rows"]["count"] = 999
    now[0] += 5
    second, second_meta = cache.get("db", load)

    assert len(calls) == 1
    assert second == {"rows": {"count": 3}}
    assert first_meta["status"] == second_meta["status"] == "FRESH"
    assert second_meta["age_seconds"] == 5


def test_cache_refreshes_after_ttl_without_blocking_stale_readers(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: now[0])
    cache = BoundedPersistedSummaryCache(ttl_seconds=5, max_stale_seconds=20)
    calls = []

    def load():
        calls.append(1)
        return {"generation": len(calls)}

    assert cache.get("db", load)[0] == {"generation": 1}
    now[0] += 6

    started, release = Event(), Event()

    def slow_refresh():
        calls.append(1)
        started.set()
        assert release.wait(2)
        return {"generation": len(calls)}

    first_stale, metadata = cache.get("db", slow_refresh)
    assert first_stale == {"generation": 1}
    assert metadata["status"] == "STALE"
    assert metadata["refresh_in_progress"] is True
    assert started.wait(1)

    # A second caller returns stale immediately while SQLite-like work is blocked.
    began = time.perf_counter()
    second_stale, second_meta = cache.get("db", slow_refresh)
    elapsed = time.perf_counter() - began
    assert second_stale == {"generation": 1}
    assert second_meta["status"] == "STALE"
    assert elapsed < 0.1
    assert len(calls) == 2

    release.set()
    _wait_for_refresh(cache)
    refreshed, refreshed_meta = cache.get("db", slow_refresh)
    assert refreshed == {"generation": 2}
    assert refreshed_meta["status"] == "FRESH"
    assert len(calls) == 2


def test_cold_refresh_is_single_flight_and_wait_is_bounded():
    cache = BoundedPersistedSummaryCache(
        ttl_seconds=5, max_stale_seconds=20, wait_seconds=0.02
    )
    started, release = Event(), Event()
    calls = []

    def slow_loader():
        calls.append(1)
        started.set()
        assert release.wait(2)
        return {"value": "ready"}

    with pytest.raises(TimeoutError, match="within its bound"):
        cache.get("db", slow_loader)
    assert started.wait(1)

    # A concurrent cold caller joins the same in-flight operation.
    with pytest.raises(TimeoutError, match="within its bound"):
        cache.get("db", slow_loader)
    assert len(calls) == 1

    release.set()
    _wait_for_refresh(cache)
    assert cache.get("db", slow_loader)[0] == {"value": "ready"}
    assert len(calls) == 1


def test_cache_serves_explicitly_stale_summary_after_refresh_failure(monkeypatch):
    now = [50.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: now[0])
    cache = BoundedPersistedSummaryCache(ttl_seconds=5, max_stale_seconds=30)
    cache.get("db", lambda: {"rows": 7})
    now[0] += 10

    def fail():
        raise OSError("sqlite unavailable")

    value, metadata = cache.get("db", fail)
    assert value == {"rows": 7}
    assert metadata["status"] == "STALE"
    assert metadata["refresh_in_progress"] is True

    _wait_for_refresh(cache)
    value, metadata = cache.get("db", fail)
    assert value == {"rows": 7}
    assert metadata["status"] == "STALE"
    assert metadata["age_seconds"] == 10
    assert metadata["refresh_errors"] == 1
    assert metadata["last_error_type"] == "OSError"


def test_cache_does_not_serve_summary_beyond_max_stale_age(monkeypatch):
    now = [50.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: now[0])
    cache = BoundedPersistedSummaryCache(
        ttl_seconds=5, max_stale_seconds=10, wait_seconds=1
    )
    cache.get("db", lambda: {"rows": 7})
    now[0] += 11

    with pytest.raises(RuntimeError, match="OSError"):
        cache.get("db", lambda: (_ for _ in ()).throw(OSError("sqlite unavailable")))


def test_cache_bounds_number_of_database_keys(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: now[0])
    cache = BoundedPersistedSummaryCache(
        ttl_seconds=5, max_stale_seconds=10, max_entries=2
    )
    for index in range(3):
        now[0] += 1
        cache.get(f"db-{index}", lambda index=index: {"value": index})

    assert len(cache._entries) == 2
    assert "db-0" not in cache._entries


def test_cache_failure_metadata_is_scoped_to_database_key(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: now[0])
    cache = BoundedPersistedSummaryCache(ttl_seconds=5, max_stale_seconds=30)

    cache.get("db-a", lambda: {"value": "a"})
    now[0] += 6
    cache.get("db-a", lambda: (_ for _ in ()).throw(OSError("a unavailable")))
    _wait_for_refresh(cache, "db-a")

    cache.get("db-b", lambda: {"value": "b"})
    value, metadata = cache.get("db-a", lambda: {"value": "unexpected"})
    assert value == {"value": "a"}
    assert metadata["last_error_type"] == "OSError"
    assert cache.get("db-b", lambda: {})[1]["last_error_type"] is None


def test_clear_during_refresh_prevents_old_worker_repopulating_cache(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: now[0])
    cache = BoundedPersistedSummaryCache(ttl_seconds=1, max_stale_seconds=10)
    cache.get("db", lambda: {"value": "old"})
    now[0] += 2

    started, release = Event(), Event()

    def slow_loader():
        started.set()
        assert release.wait(2)
        return {"value": "obsolete"}

    assert cache.get("db", slow_loader)[0] == {"value": "old"}
    assert started.wait(1)
    cache.clear()
    release.set()
    time.sleep(0.03)

    assert "db" not in cache._entries
    assert cache.get("db", lambda: {"value": "new"})[0] == {"value": "new"}


def test_refresh_thread_start_failure_is_reported_and_does_not_stick(monkeypatch):
    class FailedThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread capacity exhausted")

    monkeypatch.setattr(cache_module, "Thread", FailedThread)
    cache = BoundedPersistedSummaryCache(ttl_seconds=5, max_stale_seconds=20)

    with pytest.raises(RuntimeError, match="persisted summary refresh failed"):
        cache.get("db", lambda: {"value": "never"})

    assert cache._refreshing == {}
    assert cache._refresh_errors == 1
