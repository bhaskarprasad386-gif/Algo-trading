from datetime import datetime
from zoneinfo import ZoneInfo

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.market_data.daily_shard_catalog import DailyMarketDataShardCatalog


IST = ZoneInfo("Asia/Kolkata")


def _record(day: int, instrument: str, source: str, timeframe: str = "1s"):
    timestamp = int(datetime(2026, 11, day, 10, 0, tzinfo=IST).timestamp() * 1_000_000_000)
    return HistoricalRecord(
        source=source,
        instrument=instrument,
        timeframe=timeframe,
        timestamp_ns=timestamp,
        payload={"ltp": 100.0},
    )


def test_historical_catalog_source_summary_uses_watermark_index(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "history.sqlite3"))
    try:
        catalog.ingest(
            [
                _record(2, "NSE:NIFTY", "angelone-live-1s"),
                _record(2, "NFO:123", "angelone-live-1s"),
                _record(2, "NSE:NIFTY", "angelone-live-1s", timeframe="event"),
            ]
        )

        summary = catalog.source_summary(source="angelone-live-1s", timeframe="1s")

        assert summary == {
            "record_count": 2,
            "instrument_names": ("NFO:123", "NSE:NIFTY"),
            "latest_timestamp_ns": _record(2, "NSE:NIFTY", "angelone-live-1s").timestamp_ns,
        }
    finally:
        catalog.close()


def test_daily_shard_source_summary_deduplicates_instruments_across_days(tmp_path):
    catalog = DailyMarketDataShardCatalog(tmp_path / "live.sqlite3")
    try:
        catalog.ingest(
            [
                _record(2, "NSE:NIFTY", "angelone-live-1s"),
                _record(2, "NFO:123", "angelone-live-1s"),
                _record(3, "NSE:NIFTY", "angelone-live-1s"),
            ]
        )

        summary = catalog.source_summary(source="angelone-live-1s", timeframe="1s")

        assert summary["record_count"] == 3
        assert summary["instruments"] == 2
        assert summary["latest_timestamp_ns"] == _record(
            3, "NSE:NIFTY", "angelone-live-1s"
        ).timestamp_ns
    finally:
        catalog.close()


def test_daily_shard_source_summary_returns_empty_summary_without_shards(tmp_path):
    catalog = DailyMarketDataShardCatalog(tmp_path / "live.sqlite3")
    try:
        assert catalog.source_summary(
            source="angelone-live-1s", timeframe="1s"
        ) == {
            "record_count": 0,
            "instruments": 0,
            "latest_timestamp_ns": None,
        }
    finally:
        catalog.close()
