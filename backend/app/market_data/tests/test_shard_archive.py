from datetime import date, datetime
import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.market_data.shard_archive import archive_completed_shard, restore_archived_shard


IST = ZoneInfo("Asia/Kolkata")


def _make_shard(path):
    catalog = HistoricalCatalog(str(path))
    catalog.ingest([
        HistoricalRecord(
            "angelone-live-1s",
            "NSE:ABC|1",
            "1s",
            int(datetime(2026, 9, 30, 9, 15, tzinfo=IST).timestamp() * 1_000_000_000),
            {"close": 100},
        )
    ])
    catalog.close()


def test_archive_completed_shard_creates_verified_manifest_and_keeps_source(tmp_path):
    source = tmp_path / "backtest_market_data_2026_09_30.sqlite3"
    archive_root = tmp_path / "archive"
    _make_shard(source)
    source_bytes = source.read_bytes()

    manifest = archive_completed_shard(
        source, archive_root, date(2026, 9, 30),
        now=datetime(2026, 10, 1, 10, 0, tzinfo=IST),
    )

    archived = archive_root / "2026_09_30" / source.name
    assert archived.is_file()
    assert source.read_bytes() == source_bytes
    assert manifest.sha256
    assert manifest.integrity_check == "ok"

    manifest_file = archived.with_name(archived.name + ".manifest.json")
    data = json.loads(manifest_file.read_text())
    assert data["trading_date"] == "2026-09-30"
    assert data["sha256"] == manifest.sha256
    assert data["size_bytes"] == archived.stat().st_size


def test_current_day_cannot_be_archived(tmp_path):
    source = tmp_path / "backtest_market_data_2026_10_01.sqlite3"
    _make_shard(source)
    with pytest.raises(ValueError, match="current or future"):
        archive_completed_shard(
            source, tmp_path / "archive", date(2026, 10, 1),
            now=datetime(2026, 10, 1, 10, 0, tzinfo=IST),
        )


def test_archive_is_idempotent_when_manifest_and_checksum_match(tmp_path):
    source = tmp_path / "backtest_market_data_2026_09_30.sqlite3"
    archive_root = tmp_path / "archive"
    _make_shard(source)
    first = archive_completed_shard(
        source, archive_root, date(2026, 9, 30),
        now=datetime(2026, 10, 1, 10, 0, tzinfo=IST),
    )
    second = archive_completed_shard(
        source, archive_root, date(2026, 9, 30),
        now=datetime(2026, 10, 1, 11, 0, tzinfo=IST),
    )
    assert second == first


def test_restore_verifies_checksum_and_integrity(tmp_path):
    source = tmp_path / "backtest_market_data_2026_09_30.sqlite3"
    archive_root = tmp_path / "archive"
    _make_shard(source)
    archive_completed_shard(
        source, archive_root, date(2026, 9, 30),
        now=datetime(2026, 10, 1, 10, 0, tzinfo=IST),
    )
    archived = archive_root / "2026_09_30" / source.name
    restored = restore_archived_shard(
        archived, tmp_path / "restored" / source.name
    )
    connection = sqlite3.connect(restored)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM data_catalog").fetchone()[0] == 1
    finally:
        connection.close()


def test_restore_rejects_tampered_archive(tmp_path):
    source = tmp_path / "backtest_market_data_2026_09_30.sqlite3"
    archive_root = tmp_path / "archive"
    _make_shard(source)
    archive_completed_shard(
        source, archive_root, date(2026, 9, 30),
        now=datetime(2026, 10, 1, 10, 0, tzinfo=IST),
    )
    archived = archive_root / "2026_09_30" / source.name
    with archived.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(ValueError, match="checksum"):
        restore_archived_shard(archived, tmp_path / "restored.sqlite3")
