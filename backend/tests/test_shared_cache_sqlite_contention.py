from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.backtesting.historical_catalog import HistoricalRecord
from app.market_data.daily_shard_catalog import DailyMarketDataShardCatalog
from app.market_data.shared_cache import (
    BoundedMarketDataCache,
    cache_key_for_instrument,
    put_market_data_record,
)


def test_common_market_data_snapshot_is_published_under_canonical_key():
    token = "cache-integration-test-991827"
    payload = {"ltp": 123.45, "bid": 123.4, "ask": 123.5}
    record = SimpleNamespace(
        instrument=SimpleNamespace(exchange="nfo", token=token),
        timestamp_ns=1_800_000_000_000_000_000,
        as_dict=lambda: dict(payload),
    )
    cache = BoundedMarketDataCache(max_entries=10, ttl_seconds=60)

    key = put_market_data_record(record, cache=cache)

    assert key == cache_key_for_instrument("NFO", token) == f"NFO:{token}"
    assert cache.get(key) == payload

def test_separate_catalog_instances_serialize_writes_to_same_daily_shard(tmp_path):
    base_path = str(tmp_path / "live_market.sqlite3")
    first = DailyMarketDataShardCatalog(base_path)
    second = DailyMarketDataShardCatalog(base_path)
    timestamp_ns = 1_800_000_000_000_000_000

    batches = [
        [
            HistoricalRecord(
                source="angelone-live-1s",
                instrument=f"NFO:TEST-{writer}-{index}",
                timeframe="1s",
                timestamp_ns=timestamp_ns,
                payload={"ltp": 100.0 + index},
            )
            for index in range(40)
        ]
        for writer in range(2)
    ]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            inserted = list(pool.map(
                lambda args: args[0].ingest_if_absent_batch(args[1]),
                [(first, batches[0]), (second, batches[1])],
            ))
        assert inserted == [40, 40]
        assert first.count(source="angelone-live-1s", timeframe="1s") == 80
    finally:
        first.close()
        second.close()


def test_same_record_from_two_catalog_instances_is_idempotent(tmp_path):
    base_path = str(tmp_path / "live_market.sqlite3")
    first = DailyMarketDataShardCatalog(base_path)
    second = DailyMarketDataShardCatalog(base_path)
    record = HistoricalRecord(
        source="angelone-live-1s",
        instrument="NFO:SHARED-DUPLICATE",
        timeframe="1s",
        timestamp_ns=1_800_000_000_000_000_000,
        payload={"ltp": 100.0},
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            inserted = list(pool.map(
                lambda catalog: catalog.ingest_if_absent_batch([record]),
                [first, second],
            ))
        assert sorted(inserted) == [0, 1]
        assert first.count(source="angelone-live-1s", instrument="NFO:SHARED-DUPLICATE", timeframe="1s") == 1
    finally:
        first.close()
        second.close()
