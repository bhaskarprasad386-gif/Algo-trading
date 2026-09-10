from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.historical_sync import HistoricalSyncPlan


class EventRestartProvider:
    def __init__(self, records):
        self.records = tuple(records)
        self.calls = []

    def fetch(self, request):
        self.calls.append((request.start_ns, request.end_ns))
        selected = [
            record
            for record in self.records
            if request.start_ns <= record.timestamp_ns <= request.end_ns
        ]
        if len(self.calls) == 1:
            return iter((selected[2], selected[0], selected[2]))
        return iter(reversed(selected))


def test_high_resolution_events_survive_durable_restart_without_losing_sequence(tmp_path):
    records = (
        HistoricalRecord("feed", "NFO:101", "orderbook", 1_000, {"side": "bid", "price": 99}, 1),
        HistoricalRecord("feed", "NFO:101", "orderbook", 1_000, {"side": "ask", "price": 101}, 2),
        HistoricalRecord("feed", "NFO:101", "orderbook", 1_001, {"side": "bid", "price": 100}, 3),
    )
    request = HistoricalFetchRequest("feed", "NFO:101", "orderbook", 1_000, 1_001)
    plan = HistoricalSyncPlan((request,))
    catalog_path = tmp_path / "events.sqlite"
    jobs_path = tmp_path / "events_jobs.sqlite"
    provider = EventRestartProvider(records)

    catalog = HistoricalCatalog(str(catalog_path))
    jobs = HistoricalJobStore(str(jobs_path))
    executor = ResumableHistoricalExecutor(HistoricalIngestionService(catalog), sleep=lambda _: None)

    def complete(req):
        return catalog.event_count(
            source=req.source, instrument=req.instrument, timeframe=req.timeframe
        ) == len(records)

    first = executor.run_durable(
        provider, plan, job_store=jobs, job_id="event-restart", run_id="run-1",
        retry_attempts=1, batch_size=2, should_skip=complete,
        should_accept=lambda req, result: complete(req),
    )
    assert first.failed_request_index == 0
    assert catalog.events(source="feed", instrument="NFO:101", timeframe="orderbook") == (records[0],)
    catalog.close()
    jobs.close()

    catalog = HistoricalCatalog(str(catalog_path))
    jobs = HistoricalJobStore(str(jobs_path))
    executor = ResumableHistoricalExecutor(HistoricalIngestionService(catalog), sleep=lambda _: None)
    second = executor.run_durable(
        provider, plan, job_store=jobs, job_id="event-restart", run_id="run-1",
        retry_attempts=1, batch_size=2, should_skip=complete,
        should_accept=lambda req, result: complete(req),
    )

    assert second.failed_request_index is None
    assert jobs.chunk_state("event-restart", 0)[0] == "completed"
    assert jobs.get("event-restart").state == "completed"
    assert catalog.events(source="feed", instrument="NFO:101", timeframe="orderbook") == records
    assert provider.calls == [(1_000, 1_001), (1_000, 1_001)]
