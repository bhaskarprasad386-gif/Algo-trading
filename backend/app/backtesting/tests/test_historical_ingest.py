from __future__ import annotations

import pytest

from backend.app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from backend.app.backtesting.historical_ingest import (
    HistoricalFetchRequest,
    HistoricalIngestionService,
)


class FakeSource:
    def __init__(self, records: list[HistoricalRecord]) -> None:
        self.records = records
        self.requests: list[HistoricalFetchRequest] = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        return [
            record
            for record in self.records
            if request.start_ns <= record.timestamp_ns <= request.end_ns
        ]


def record(ts: int) -> HistoricalRecord:
    return HistoricalRecord("angel", "NIFTY", "1m", ts, {"close": ts})


def test_next_request_uses_watermark_plus_interval() -> None:
    catalog = HistoricalCatalog()
    catalog.ingest([record(100), record(200)])
    service = HistoricalIngestionService(catalog)

    request = service.next_request(
        source="angel", instrument="NIFTY", timeframe="1m", end_ns=500, interval_ns=100
    )

    assert request.start_ns == 300
    assert request.end_ns == 500


def test_sync_merges_and_deduplicates_source_batch() -> None:
    catalog = HistoricalCatalog()
    service = HistoricalIngestionService(catalog)
    source = FakeSource([record(100), record(200), record(300)])
    request = HistoricalFetchRequest("angel", "NIFTY", "1m", 100, 300)

    first = service.sync(source, request)
    second = service.sync(source, request)

    assert first.fetched == 3
    assert first.inserted == 3
    assert second.fetched == 3
    assert second.inserted == 0
    assert catalog.count(source="angel", instrument="NIFTY") == 3
    assert catalog.watermark(source="angel", instrument="NIFTY", timeframe="1m") == 300


def test_sync_rejects_records_outside_request() -> None:
    catalog = HistoricalCatalog()
    service = HistoricalIngestionService(catalog)

    class BadSource:
        def fetch(self, request):
            return [record(999)]

    with pytest.raises(ValueError, match="outside the requested range"):
        service.sync(BadSource(), HistoricalFetchRequest("angel", "NIFTY", "1m", 100, 300))


def test_repair_ranges_delegates_to_catalog() -> None:
    catalog = HistoricalCatalog()
    catalog.ingest([record(100), record(300)])
    service = HistoricalIngestionService(catalog)

    assert service.repair_ranges(
        source="angel", instrument="NIFTY", timeframe="1m", interval_ns=100
    )[0].start_ns == 200
