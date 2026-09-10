from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.historical_sync import HistoricalSyncPlan


class _MultiChunkEventSource:
    def __init__(self):
        self.failed = False

    def fetch(self, request):
        if request.start_ns == 200 and not self.failed:
            self.failed = True
            return iter(
                (
                    HistoricalRecord(request.source, request.instrument, request.timeframe, 200, {"ltp": 200}, 1),
                    HistoricalRecord(request.source, request.instrument, request.timeframe, 250, {"ltp": 250}, 1),
                )
            )
        return tuple(
            HistoricalRecord(request.source, request.instrument, request.timeframe, ts, {"ltp": ts}, 1)
            for ts in (request.start_ns, request.end_ns)
        )


def test_multi_chunk_event_recovery_keeps_completed_chunks_and_replays_failed_chunk(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "events.db"))
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    service = HistoricalIngestionService(catalog)
    source = _MultiChunkEventSource()
    plan = HistoricalSyncPlan(
        requests=(
            HistoricalFetchRequest("feed", "NFO:101", "tick", 100, 150),
            HistoricalFetchRequest("feed", "NFO:101", "tick", 200, 250),
            HistoricalFetchRequest("feed", "NFO:101", "tick", 300, 350),
        )
    )
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None, collect_results=False)

    first = executor.run_durable(
        source,
        plan,
        job_store=store,
        job_id="multi-chunk-event-job",
        run_id="multi-chunk-event-run",
        retry_attempts=1,
        batch_size=1,
    )
    assert first.failed_request_index == 1
    assert store.chunk_state("multi-chunk-event-job", 0)[0] == "completed"
    assert store.chunk_state("multi-chunk-event-job", 1)[0] == "recoverable"
    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 3

    second = executor.run_durable(
        source,
        plan,
        job_store=store,
        job_id="multi-chunk-event-job",
        run_id="multi-chunk-event-run",
        retry_attempts=1,
        batch_size=1,
    )
    assert second.failed_request_index is None
    assert store.chunk_state("multi-chunk-event-job", 0)[0] == "completed"
    assert store.chunk_state("multi-chunk-event-job", 1)[0] == "completed"
    assert store.chunk_state("multi-chunk-event-job", 2)[0] == "completed"
    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 6
    assert tuple(event.timestamp_ns for event in catalog.events(source="feed", instrument="NFO:101", timeframe="tick")) == (
        100, 150, 200, 250, 300, 350
    )
