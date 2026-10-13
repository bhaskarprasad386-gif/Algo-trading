from concurrent.futures import ThreadPoolExecutor

from app.backtesting.historical_catalog import HistoricalRecord
from app.market_data.persistence import DailySQLiteMarketDataRepository


def test_independent_repositories_serialize_writes_to_same_daily_shard(tmp_path):
    base_path = str(tmp_path / "live-market.sqlite3")
    repositories = [DailySQLiteMarketDataRepository(base_path) for _ in range(4)]
    records_per_repository = 100

    def write_from_repository(index):
        repository = repositories[index]
        total = 0
        for start in range(0, records_per_repository, 10):
            records = [
                HistoricalRecord(
                    source="angelone-live-1s",
                    instrument=f"TEST-{index}-{item}",
                    timeframe="1s",
                    timestamp_ns=1_800_000_000_000_000_000 + item * 1_000_000_000,
                    payload={"ltp": 100 + item, "writer": index},
                )
                for item in range(start, start + 10)
            ]
            total += repository.write_batch(records)
        return total

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            inserted = list(pool.map(write_from_repository, range(4)))

        reader = DailySQLiteMarketDataRepository(base_path)
        try:
            assert sum(inserted) == 4 * records_per_repository
            assert reader.catalog.count(
                source="angelone-live-1s", timeframe="1s"
            ) == 4 * records_per_repository
        finally:
            reader.close()
    finally:
        for repository in repositories:
            repository.close()
