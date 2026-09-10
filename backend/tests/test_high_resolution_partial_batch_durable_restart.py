from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.historical_sync import HistoricalSyncPlan
from app.backtesting.historical_ingest import HistoricalFetchRequest


class _RestartingEventSource:
    def __init__(self):
        self.first_run = True

    def fetch(self, request):
        if self.first_run:
            self.first_run = False

            def interrupted():
                yield HistoricalRecord(
                    request.source,
                    request.instrument,
                    request.timeframe,
                    200,
                    {"ltp": 200},
                    1,
                )
                raise RuntimeError("simulated mid-stream interruption")

            return interrupted()

        return (
            HistoricalRecord(
                request.source,
                request.instrument,
                request.timeframe,
                200,
                {"ltp": 200},
                1,
            ),
            HistoricalRecord(
                request.source,
                request.instrument,
                request.timeframe,
                300,
                {"ltp": 300},
                1,
            ),
        )


def test_partial_event_batch_restart_replays_without_duplicate(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "events.db"))
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    service = HistoricalIngestionService(catalog)
    source = _RestartingEventSource()
    plan = HistoricalSyncPlan(
        requests=(HistoricalFetchRequest("feed", "NFO:101", "tick", 200, 300),)
    )
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None, collect_results=False)

    first = executor.run_durable(
        source,
        plan,
        job_store=store,
        job_id="event-restart-job",
        run_id="event-restart-run",
        retry_attempts=1,
        batch_size=1,
    )
    assert first.failed_request_index == 0
    assert store.chunk_state("event-restart-job", 0)[0] == "recoverable"
    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 1

    second = executor.run_durable(
        source,
        plan,
        job_store=store,
        job_id="event-restart-job",
        run_id="event-restart-run",
        retry_attempts=1,
        batch_size=1,
    )
    assert second.failed_request_index is None
    assert store.chunk_state("event-restart-job", 0)[0] == "completed"
    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 2
    assert catalog.events(
        source="feed", instrument="NFO:101", timeframe="tick"
    ) == (
        HistoricalRecord("feed", "NFO:101", "tick", 200, {"ltp": 200}, 1),
        HistoricalRecord("feed", "NFO:101", "tick", 300, {"ltp": 300}, 1),
    )
