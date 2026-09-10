import pytest

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.historical_sync import HistoricalSyncPlan


class _ConflictingReplaySource:
    def __init__(self):
        self.first_run = True

    def fetch(self, request):
        if self.first_run:
            self.first_run = False
            return (
                HistoricalRecord(request.source, request.instrument, request.timeframe, 200, {"ltp": 200}, 1),
                HistoricalRecord(request.source, request.instrument, request.timeframe, 300, {"ltp": 300}, 1),
            )
        return (
            HistoricalRecord(request.source, request.instrument, request.timeframe, 200, {"ltp": 999}, 1),
            HistoricalRecord(request.source, request.instrument, request.timeframe, 300, {"ltp": 300}, 1),
        )


def test_conflicting_event_replay_preserves_existing_durable_data(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "events.db"))
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    service = HistoricalIngestionService(catalog)
    source = _ConflictingReplaySource()
    plan = HistoricalSyncPlan(
        requests=(HistoricalFetchRequest("feed", "NFO:101", "tick", 200, 300),)
    )
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None, collect_results=False)

    first = executor.run_durable(
        source,
        plan,
        job_store=store,
        job_id="conflict-replay-job",
        run_id="conflict-replay-run",
        retry_attempts=1,
        batch_size=2,
    )
    assert first.failed_request_index is None
    assert store.chunk_state("conflict-replay-job", 0)[0] == "completed"
    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 2

    with pytest.raises(ValueError, match="conflicting historical record identity"):
        catalog.ingest(
            (
                HistoricalRecord("feed", "NFO:101", "tick", 200, {"ltp": 999}, 1),
            )
        )

    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 2
    assert catalog.events(source="feed", instrument="NFO:101", timeframe="tick")[0] == HistoricalRecord(
        "feed", "NFO:101", "tick", 200, {"ltp": 200}, 1
    )

    with pytest.raises(ValueError, match="conflicting historical record identity"):
        executor.run_durable(
            source,
            plan,
            job_store=store,
            job_id="conflict-replay-job-2",
            run_id="conflict-replay-run-2",
            retry_attempts=1,
            batch_size=2,
        )

    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 2
    assert catalog.events(source="feed", instrument="NFO:101", timeframe="tick") == (
        HistoricalRecord("feed", "NFO:101", "tick", 200, {"ltp": 200}, 1),
        HistoricalRecord("feed", "NFO:101", "tick", 300, {"ltp": 300}, 1),
    )
