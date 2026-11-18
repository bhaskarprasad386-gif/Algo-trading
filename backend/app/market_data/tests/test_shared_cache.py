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



def test_cache_rejects_older_timestamp_for_existing_snapshot():
    cache = BoundedMarketDataCache(max_entries=10, ttl_seconds=60)
    cache.put("NSE:EQ:101", {"ltp": 101}, timestamp_ns=200)
    cache.put("NSE:EQ:101", {"ltp": 99}, timestamp_ns=100)
    assert cache.get("NSE:EQ:101")["ltp"] == 101


def test_cache_accepts_equal_timestamp_as_latest_arrival():
    cache = BoundedMarketDataCache(max_entries=10, ttl_seconds=60)
    cache.put("NSE:EQ:101", {"ltp": 101}, timestamp_ns=200)
    cache.put("NSE:EQ:101", {"ltp": 102}, timestamp_ns=200)
    assert cache.get("NSE:EQ:101")["ltp"] == 102


def test_normalized_record_publishes_canonical_instrument_key():
    from types import SimpleNamespace
    from app.market_data.shared_cache import put_market_data_record

    cache = BoundedMarketDataCache(max_entries=10, ttl_seconds=60)
    record = SimpleNamespace(
        instrument=SimpleNamespace(value="NSE:EQ:101"),
        timestamp_ns=200,
        as_dict=lambda: {"ltp": 101, "payload": {"raw": "provider"}},
    )
    key = put_market_data_record(record, cache=cache)
    assert key == "NSE:EQ:101"
    assert cache.get(key) == {"ltp": 101}
