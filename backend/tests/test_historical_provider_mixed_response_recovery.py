from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService


class MixedProvider:
    def __init__(self, records):
        self.records = tuple(records)
        self.calls = []

    def fetch(self, request):
        self.calls.append((request.start_ns, request.end_ns))
        selected = [r for r in self.records if request.start_ns <= r.timestamp_ns <= request.end_ns]
        if len(self.calls) == 1:
            # First response is deliberately unordered and contains duplicates,
            # while omitting one interior record so the chunk is retried.
            selected = [r for r in reversed(selected) if r.timestamp_ns != 200]
            selected.append(selected[0])
        return iter(selected)


def test_mixed_missing_duplicate_out_of_order_response_is_repaired_durably():
    records = tuple(
        HistoricalRecord("provider", "NFO:101", "1m", ts, {"close": ts})
        for ts in (100, 200, 300, 400)
    )
    provider = MixedProvider(records)
    catalog = HistoricalCatalog()
    service = HistoricalIngestionService(catalog)
    executor = ResumableHistoricalExecutor()
    request = HistoricalFetchRequest("provider", "NFO:101", "1m", 100, 400)

    result = service.sync_streaming(
        provider.fetch,
        request,
        batch_size=2,
        executor=executor,
        retry_attempts=2,
    )

    assert result.inserted == 4
    assert catalog.records(source="provider", instrument="NFO:101", timeframe="1m") == records
    assert provider.calls == [(100, 400), (100, 400)]
