import pytest

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_conflicting_record_rolls_back_only_the_active_batch(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "catalog.sqlite"))
    first_batch = (
        HistoricalRecord("provider", "NFO:101", "1m", 100, {"close": 100}),
        HistoricalRecord("provider", "NFO:101", "1m", 200, {"close": 200}),
    )
    assert catalog.ingest(first_batch) == 2

    conflicting_batch = (
        HistoricalRecord("provider", "NFO:101", "1m", 300, {"close": 300}),
        HistoricalRecord("provider", "NFO:101", "1m", 100, {"close": 999}),
    )

    with pytest.raises(ValueError, match="conflicting historical record identity"):
        catalog.ingest(conflicting_batch)

    assert catalog.records(
        source="provider", instrument="NFO:101", timeframe="1m"
    ) == first_batch
    assert catalog.count(source="provider", instrument="NFO:101", timeframe="1m") == 2
    assert catalog.watermark(
        source="provider", instrument="NFO:101", timeframe="1m"
    ) == 200
