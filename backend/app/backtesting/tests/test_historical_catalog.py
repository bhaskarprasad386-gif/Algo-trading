import pytest

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def record(ts, price=100, sequence=None):
    return HistoricalRecord("vendor", "NIFTY", "1m", ts, {"price": price}, sequence)


def test_incremental_ingestion_deduplicates_and_updates_watermark():
    catalog = HistoricalCatalog()
    assert catalog.ingest([record(60), record(120)]) == 2
    assert catalog.ingest([record(60), record(120), record(180)]) == 1
    assert catalog.count() == 3
    assert catalog.watermark(source="vendor", instrument="NIFTY", timeframe="1m") == 180
    catalog.close()


def test_same_identity_with_changed_payload_is_rejected_atomically():
    catalog = HistoricalCatalog()
    catalog.ingest([record(60), record(120)])
    with pytest.raises(ValueError, match="conflicting historical record identity"):
        catalog.ingest([record(180), record(120, price=101), record(240)])
    assert catalog.count() == 2
    catalog.close()


def test_gap_detection_returns_repair_ranges():
    catalog = HistoricalCatalog()
    catalog.ingest([record(60), record(120), record(300), record(360)])
    assert catalog.gaps(source="vendor", instrument="NIFTY", timeframe="1m", interval_ns=60) == (
        # 180..240 are the missing cadence points between 120 and 300.
        catalog.gaps(source="vendor", instrument="NIFTY", timeframe="1m", interval_ns=60)[0],
    )
    gap = catalog.gaps(source="vendor", instrument="NIFTY", timeframe="1m", interval_ns=60)[0]
    assert (gap.start_ns, gap.end_ns) == (180, 240)
    catalog.close()


def test_sequence_allows_multiple_source_events_at_same_timestamp():
    catalog = HistoricalCatalog()
    assert catalog.ingest([record(100, sequence=1), record(100, price=101, sequence=2)]) == 2
    assert [r.sequence for r in catalog.records(source="vendor", instrument="NIFTY", timeframe="1m")] == [1, 2]
    catalog.close()
