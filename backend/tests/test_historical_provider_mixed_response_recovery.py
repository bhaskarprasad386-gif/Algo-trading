from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.backtesting.historical_sync import HistoricalSyncPlan


class MixedProvider:
    def __init__(self, records):
        self.records = tuple(records)
        self.calls = []

    def fetch(self, request):
        self.calls.append((request.start_ns, request.end_ns))
        selected = [r for r in self.records if request.start_ns <= r.timestamp_ns <= request.end_ns]
        if len(self.calls) == 1:
            # First response is deliberately unordered and contains duplicates,
            # while omitting one interior record so the chunk is rejected and retried.
            selected = [r for r in reversed(selected) if r.timestamp_ns != 200]
            selected.append(selected[0])
        return iter(selected)


def test_mixed_missing_duplicate_out_of_order_response_is_retried():
    records = tuple(
        HistoricalRecord("provider", "NFO:101", "1m", ts, {"close": ts})
        for ts in (100, 200, 300, 400)
    )
    provider = MixedProvider(records)
    catalog = HistoricalCatalog()
    service = HistoricalIngestionService(catalog)
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None)
    request = HistoricalFetchRequest("provider", "NFO:101", "1m", 100, 400)
    plan = HistoricalSyncPlan((request,))

    def complete(req):
        return catalog.records(
            source=req.source, instrument=req.instrument, timeframe=req.timeframe
        ) == records

    result = executor.run(
        provider,
        plan,
        retry_attempts=2,
        retry_delay_seconds=0,
        batch_size=2,
        should_accept=lambda req, _result: complete(req),
    )

    assert result.failed_request_index is None
    assert result.completed_count == 1
    assert catalog.records(source="provider", instrument="NFO:101", timeframe="1m") == records
    assert provider.calls == [(100, 400), (100, 400)]
