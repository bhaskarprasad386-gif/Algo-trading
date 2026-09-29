from datetime import date, datetime
from pathlib import Path

from app.backtesting.historical_catalog import HistoricalRecord
from app.market_data.daily_shard_catalog import DailyMarketDataShardCatalog, shard_path


def _ns(day: date, second: int) -> int:
    return int(datetime(day.year, day.month, day.day, 9, 15, second, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).timestamp() * 1_000_000_000)


def test_shard_path_is_date_partitioned(tmp_path):
    base = tmp_path / "backtest_market_data.sqlite3"
    assert shard_path(base, date(2026, 9, 30)).name == "backtest_market_data_2026_09_30.sqlite3"


def test_live_records_are_split_by_trading_day_and_read_across_shards(tmp_path):
    base = tmp_path / "backtest_market_data.sqlite3"
    catalog = DailyMarketDataShardCatalog(base)
    try:
        records = [
            HistoricalRecord("angelone-live-1s", "NSE:ABC|1", "1s", _ns(date(2026, 9, 30), 1), {"close": 100}),
            HistoricalRecord("angelone-live-1s", "NSE:ABC|1", "1s", _ns(date(2026, 10, 1), 1), {"close": 101}),
        ]
        assert catalog.ingest_if_absent_batch(records) == 2
        assert len(catalog.shard_paths()) == 2
        assert catalog.count(source="angelone-live-1s", timeframe="1s") == 2
        assert [r.payload["close"] for r in catalog.iter_records(source="angelone-live-1s", instrument="NSE:ABC|1", timeframe="1s")] == [100, 101]
    finally:
        catalog.close()


def test_legacy_base_data_remains_readable_with_new_shards(tmp_path):
    base = tmp_path / "backtest_market_data.sqlite3"
    from app.backtesting.historical_catalog import HistoricalCatalog
    legacy = HistoricalCatalog(str(base))
    legacy.ingest([HistoricalRecord("legacy", "ABC", "1m", 1_000_000_000, {"close": 99})])
    legacy.close()
    catalog = DailyMarketDataShardCatalog(base)
    try:
        catalog.ingest([HistoricalRecord("angelone-live-1s", "ABC", "1s", _ns(date(2026, 9, 30), 2), {"close": 100})])
        assert catalog.count() == 2
        assert catalog.watermark(source="legacy", instrument="ABC", timeframe="1m") == 1_000_000_000
    finally:
        catalog.close()
