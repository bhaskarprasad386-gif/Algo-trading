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


def test_read_source_summary_uses_read_only_connections_for_legacy_and_daily_shards(
    monkeypatch, tmp_path
):
    import sqlite3
    import app.market_data.daily_shard_catalog as shard_module

    base_path = tmp_path / "live.sqlite3"
    legacy = HistoricalCatalog(str(base_path))
    try:
        legacy.ingest(_record(1, "NSE:NIFTY", "angelone-live-1s"))
    finally:
        legacy.close()

    writer = DailyMarketDataShardCatalog(base_path)
    try:
        writer.ingest(
            [
                _record(2, "NSE:NIFTY", "angelone-live-1s"),
                _record(2, "NFO:123", "angelone-live-1s"),
            ]
        )
    finally:
        writer.close()

    real_connect = sqlite3.connect
    opened = []

    def observe_connect(database, *args, **kwargs):
        opened.append((database, kwargs.get("uri", False)))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(shard_module.sqlite3, "connect", observe_connect)
    summary = DailyMarketDataShardCatalog.read_source_summary(
        base_path, source="angelone-live-1s", timeframe="1s"
    )

    assert summary["record_count"] == 3
    assert summary["instruments"] == 2
    assert summary["latest_timestamp_ns"] == _record(
        2, "NSE:NIFTY", "angelone-live-1s"
    ).timestamp_ns
    assert len(opened) == 2
    assert all(uri and "?mode=ro" in database for database, uri in opened)


def test_read_source_summary_does_not_initialize_an_empty_legacy_database(tmp_path):
    import sqlite3

    base_path = tmp_path / "empty.sqlite3"
    connection = sqlite3.connect(base_path)
    connection.close()

    summary = DailyMarketDataShardCatalog.read_source_summary(
        base_path, source="angelone-live-1s", timeframe="1s"
    )

    check = sqlite3.connect(base_path)
    try:
        tables = {
            row[0] for row in check.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        check.close()

    assert summary == {
        "record_count": 0,
        "instruments": 0,
        "latest_timestamp_ns": None,
    }
    assert tables == set()


def test_read_source_summary_falls_back_when_legacy_watermarks_are_unpopulated(tmp_path):
    import sqlite3

    base_path = tmp_path / "legacy.sqlite3"
    catalog = HistoricalCatalog(str(base_path))
    try:
        catalog.ingest(_record(2, "NSE:NIFTY", "angelone-live-1s"))
    finally:
        catalog.close()

    connection = sqlite3.connect(base_path)
    try:
        connection.execute("DELETE FROM source_watermarks")
        connection.commit()
    finally:
        connection.close()

    summary = DailyMarketDataShardCatalog.read_source_summary(
        base_path, source="angelone-live-1s", timeframe="1s"
    )

    assert summary["record_count"] == 1
    assert summary["instruments"] == 1
    assert summary["latest_timestamp_ns"] == _record(
        2, "NSE:NIFTY", "angelone-live-1s"
    ).timestamp_ns
