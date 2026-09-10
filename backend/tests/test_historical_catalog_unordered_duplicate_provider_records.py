from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_unordered_duplicate_provider_records_are_deduplicated_and_ordered():
    catalog = HistoricalCatalog()
    records = [
        HistoricalRecord("provider", "NFO:101", "1m", 300, {"close": 103}),
        HistoricalRecord("provider", "NFO:101", "1m", 100, {"close": 101}),
        HistoricalRecord("provider", "NFO:101", "1m", 200, {"close": 102}),
        HistoricalRecord("provider", "NFO:101", "1m", 200, {"close": 102}),
        HistoricalRecord("provider", "NFO:101", "1m", 100, {"close": 101}),
    ]

    assert catalog.ingest(records) == 3
    assert catalog.count(source="provider", instrument="NFO:101", timeframe="1m") == 3
    assert tuple(record.timestamp_ns for record in catalog.records(
        source="provider", instrument="NFO:101", timeframe="1m"
    )) == (100, 200, 300)


def test_conflicting_duplicate_identity_is_rejected_without_partial_write():
    catalog = HistoricalCatalog()
    original = HistoricalRecord("provider", "NFO:101", "1m", 100, {"close": 101})
    conflicting = HistoricalRecord("provider", "NFO:101", "1m", 100, {"close": 999})

    assert catalog.ingest([original]) == 1

    try:
        catalog.ingest([conflicting, HistoricalRecord("provider", "NFO:101", "1m", 200, {"close": 102})])
    except ValueError as exc:
        assert "conflicting historical record identity" in str(exc)
    else:
        raise AssertionError("conflicting duplicate identity must be rejected")

    assert catalog.count(source="provider", instrument="NFO:101", timeframe="1m") == 1
