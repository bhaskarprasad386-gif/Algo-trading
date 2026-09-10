import pytest

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.historical_sync import HistoricalSyncPlan


class _MultiChunkConflictSource:
    def __init__(self):
        self.failed = False
        self.conflict = False

    def fetch(self, request):
        if request.start_ns == 200 and not self.failed:
            self.failed = True
            def interrupted():
                yield HistoricalRecord(request.source, request.instrument, request.timeframe, 200, {"ltp": 200}, 1)
                raise RuntimeError("simulated interruption")
            return interrupted()
        if request.start_ns == 200 and self.conflict:
            return (
                HistoricalRecord(request.source, request.instrument, request.timeframe, 200, {"ltp": 999}, 1),
                HistoricalRecord(request.source, request.instrument, request.timeframe, 250, {"ltp": 250}, 1),
            )
        return (
            HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"ltp": request.start_ns}, 1),
            HistoricalRecord(request.source, request.instrument, request.timeframe, request.end_ns, {"ltp": request.end_ns}, 1),
        )


def test_multi_chunk_conflicting_restart_preserves_completed_and_partial_data(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "events.db"))
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    service = HistoricalIngestionService(catalog)
    source = _MultiChunkConflictSource()
    plan = HistoricalSyncPlan(requests=(
        HistoricalFetchRequest("feed", "NFO:101", "tick", 100, 150),
        HistoricalFetchRequest("feed", "NFO:101", "tick", 200, 250),
        HistoricalFetchRequest("feed", "NFO:101", "tick", 300, 350),
    ))
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None, collect_results=False)

    first = executor.run_durable(source, plan, job_store=store, job_id="multi-conflict-job", run_id="run-1", retry_attempts=1, batch_size=1)
    assert first.failed_request_index == 1
    assert store.chunk_state("multi-conflict-job", 0)[0] == "completed"
    assert store.chunk_state("multi-conflict-job", 1)[0] == "recoverable"
    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 3

    source.conflict = True
    with pytest.raises(ValueError, match="conflicting historical record identity"):
        executor.run_durable(source, plan, job_store=store, job_id="multi-conflict-job", run_id="run-1", retry_attempts=1, batch_size=1)

    assert store.chunk_state("multi-conflict-job", 0)[0] == "completed"
    assert store.chunk_state("multi-conflict-job", 1)[0] == "recoverable"
    assert store.chunk_state("multi-conflict-job", 2)[0] == "pending"
    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 3
    assert catalog.events(source="feed", instrument="NFO:101", timeframe="tick") == (
        HistoricalRecord("feed", "NFO:101", "tick", 100, {"ltp": 100}, 1),
        HistoricalRecord("feed", "NFO:101", "tick", 150, {"ltp": 150}, 1),
        HistoricalRecord("feed", "NFO:101", "tick", 200, {"ltp": 200}, 1),
    )
