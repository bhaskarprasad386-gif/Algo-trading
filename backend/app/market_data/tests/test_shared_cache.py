from app.market_data.shared_cache import BoundedMarketDataCache


def test_cache_is_hard_bounded_and_latest_snapshot_wins():
    cache = BoundedMarketDataCache(max_entries=2, ttl_seconds=60)
    cache.put("A", {"ltp": 1})
    cache.put("A", {"ltp": 2})
    cache.put("B", {"ltp": 3})
    cache.put("C", {"ltp": 4})
    assert cache.get("A")["ltp"] == 2
    assert cache.get("B") is None
    assert cache.get("C")["ltp"] == 4
    assert cache.stats()["entries"] == 2
    assert cache.stats()["evictions"] == 1


def test_cache_defensively_copies_values():
    cache = BoundedMarketDataCache(max_entries=10, ttl_seconds=60)
    value = {"ltp": 100, "nested": {"bid": 99}}
    cache.put("A", value)
    value["nested"]["bid"] = 1
    fetched = cache.get("A")
    fetched["nested"]["bid"] = 2
    assert cache.get("A")["nested"]["bid"] == 99


def test_cache_expires_stale_snapshots():
    cache = BoundedMarketDataCache(max_entries=10, ttl_seconds=0.01)
    cache.put("A", {"ltp": 100})
    import time
    time.sleep(0.02)
    assert cache.get("A") is None
    assert cache.stats()["expired"] >= 1
