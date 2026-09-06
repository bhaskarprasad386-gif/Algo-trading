from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService, HistoricalRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_sync import HistoricalSyncPlan


class Source:
    def fetch(self, request):
        yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 1})


def test_executor_emits_start_and_complete_callbacks():
    catalog = HistoricalCatalog()
    service = HistoricalIngestionService(catalog)
    request = HistoricalFetchRequest("test", "NSE:ABC", "1m", 0, 0)
    events = []
    result = ResumableHistoricalExecutor(service, sleep=lambda _: None).run(
        Source(), HistoricalSyncPlan((request,)),
        on_chunk_start=lambda i, r, a: events.append(("start", i, a)),
        on_chunk_complete=lambda i, r, x, a: events.append(("complete", i, a)),
    )
    assert result.failed_request_index is None
    assert events == [("start", 0, 1), ("complete", 0, 1)]
    catalog.close()


def test_executor_emits_skip_callback_without_fetching():
    catalog = HistoricalCatalog()
    service = HistoricalIngestionService(catalog)
    request = HistoricalFetchRequest("test", "NSE:ABC", "1m", 0, 0)
    events = []
    result = ResumableHistoricalExecutor(service).run(
        Source(), HistoricalSyncPlan((request,)),
        should_skip=lambda _: True,
        on_chunk_skip=lambda i, r: events.append(("skip", i)),
    )
    assert result.skipped_request_indices == (0,)
    assert events == [("skip", 0)]
    assert catalog.count() == 0
    catalog.close()
