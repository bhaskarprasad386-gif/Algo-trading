from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_expected_events import missing_expected_timestamps
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_sync import build_expected_event_plan
from app.backtesting.historical_job_store import HistoricalJobStore


class _EventSource:
    def __init__(self):
        self.fail_once_for = 400

    def fetch(self, request):
        if request.start_ns == self.fail_once_for:
            self.fail_once_for = None
            raise RuntimeError("simulated provider interruption")
        return tuple(
            HistoricalRecord(
                request.source,
                request.instrument,
                request.timeframe,
                timestamp,
                {"ltp": timestamp},
            )
            for timestamp in (200, 400)
            if request.start_ns <= timestamp <= request.end_ns
        )


def test_expected_event_plan_is_durable_across_restart(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "events.db"))
    catalog.ingest_events(
        (
            HistoricalRecord("feed", "NFO:101", "tick", 100, {"ltp": 100}),
            HistoricalRecord("feed", "NFO:101", "tick", 300, {"ltp": 300}),
        )
    )
    plan = build_expected_event_plan(
        catalog,
        source="feed",
        instrument="NFO:101",
        timeframe="tick",
        expected_timestamps=(100, 200, 300, 400),
        max_request_ns=50,
    )
    assert tuple((r.start_ns, r.end_ns) for r in plan.requests) == ((200, 200), (400, 400))
    assert missing_expected_timestamps(
        catalog,
        source="feed",
        instrument="NFO:101",
        timeframe="tick",
        expected_timestamps=(100, 200, 300, 400),
    ) == (200, 400)

    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    service = HistoricalIngestionService(catalog)
    source = _EventSource()
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None, collect_results=False)

    first = executor.run_durable(
        source,
        plan,
        job_store=store,
        job_id="event-job",
        run_id="event-run",
        retry_attempts=1,
        batch_size=1,
    )
    assert first.failed_request_index == 1
    assert store.chunk_state("event-job", 0)[0] == "completed"
    assert store.chunk_state("event-job", 1)[0] == "recoverable"
    assert missing_expected_timestamps(
        catalog,
        source="feed",
        instrument="NFO:101",
        timeframe="tick",
        expected_timestamps=(100, 200, 300, 400),
    ) == (400,)

    second = executor.run_durable(
        source,
        plan,
        job_store=store,
        job_id="event-job",
        run_id="event-run",
        retry_attempts=1,
        batch_size=1,
    )
    assert second.failed_request_index is None
    assert store.chunk_state("event-job", 0)[0] == "completed"
    assert store.chunk_state("event-job", 1)[0] == "completed"
    assert missing_expected_timestamps(
        catalog,
        source="feed",
        instrument="NFO:101",
        timeframe="tick",
        expected_timestamps=(100, 200, 300, 400),
    ) == ()
    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 4
