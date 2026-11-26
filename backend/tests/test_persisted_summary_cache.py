import pytest

from app.market_data import persisted_summary_cache as cache_module
from app.market_data.persisted_summary_cache import BoundedPersistedSummaryCache


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


def test_cache_refreshes_after_ttl_and_reports_refresh_metadata(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: now[0])
    cache = BoundedPersistedSummaryCache(ttl_seconds=5, max_stale_seconds=20)
    calls = []

    def load():
        calls.append(1)
        return {"generation": len(calls)}

    assert cache.get("db", load)[0] == {"generation": 1}
    now[0] += 6
    value, metadata = cache.get("db", load)

    assert value == {"generation": 2}
    assert metadata["status"] == "FRESH"
    assert metadata["refresh_count"] == 2
    assert len(calls) == 2


def test_cache_serves_explicitly_stale_persisted_summary_after_refresh_failure(monkeypatch):
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
    assert metadata["age_seconds"] == 10
    assert metadata["refresh_errors"] == 1
    assert metadata["last_error_type"] == "OSError"


def test_cache_does_not_serve_summary_beyond_max_stale_age(monkeypatch):
    now = [50.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: now[0])
    cache = BoundedPersistedSummaryCache(ttl_seconds=5, max_stale_seconds=10)
    cache.get("db", lambda: {"rows": 7})
    now[0] += 11

    with pytest.raises(OSError):
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
