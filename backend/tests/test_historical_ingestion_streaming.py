from dataclasses import dataclass

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService


@dataclass
class Source:
    records: tuple[HistoricalRecord, ...]

    def fetch(self, request):
        return iter(self.records)


def test_sync_streaming_commits_bounded_batches_and_reports_scalar_progress(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "history.db"))
    service = HistoricalIngestionService(catalog)
    request = HistoricalFetchRequest("src", "SBIN", "1m", 0, 4)
    source = Source(
        tuple(
            HistoricalRecord("src", "SBIN", "1m", timestamp_ns=i, payload={"p": i})
            for i in range(5)
        )
    )
    progress = []

    result = service.sync_streaming(
        source,
        request,
        batch_size=2,
        on_batch=lambda inserted, fetched: progress.append((inserted, fetched)),
    )

    assert result.inserted == 5
    assert result.fetched == 5
    assert progress == [(2, 2), (4, 4), (5, 5)]
    assert result.final_watermark_ns == 4
    assert catalog.count(source="src", instrument="SBIN") == 5


def test_sync_streaming_deduplicates_on_replay_without_growing_catalog(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "history.db"))
    service = HistoricalIngestionService(catalog)
    request = HistoricalFetchRequest("src", "SBIN", "1m", 0, 2)
    source = Source(
        tuple(
            HistoricalRecord("src", "SBIN", "1m", timestamp_ns=i, payload={"p": i})
            for i in range(3)
        )
    )

    first = service.sync_streaming(source, request, batch_size=2)
    second = service.sync_streaming(source, request, batch_size=2)

    assert first.inserted == 3
    assert second.inserted == 0
    assert second.fetched == 3
    assert catalog.count(source="src", instrument="SBIN") == 3
