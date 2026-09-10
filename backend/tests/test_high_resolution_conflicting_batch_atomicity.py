import pytest

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_conflicting_event_batch_rolls_back_all_new_records(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "events.db"))
    existing = HistoricalRecord("feed", "NFO:101", "tick", 200, {"ltp": 200}, 1)
    catalog.ingest_events((existing,))

    conflicting_batch = (
        HistoricalRecord("feed", "NFO:101", "tick", 300, {"ltp": 300}, 1),
        HistoricalRecord("feed", "NFO:101", "tick", 200, {"ltp": 999}, 1),
        HistoricalRecord("feed", "NFO:101", "tick", 400, {"ltp": 400}, 1),
    )

    with pytest.raises(ValueError, match="conflicting historical record identity"):
        catalog.ingest_events(conflicting_batch)

    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 1
    assert catalog.events(source="feed", instrument="NFO:101", timeframe="tick") == (existing,)
