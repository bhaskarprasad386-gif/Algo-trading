from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalIngestionService, HistoricalFetchRequest
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.historical_sync import HistoricalSyncPlan


class RestartingProvider:
    def __init__(self, records):
        self.records = tuple(records)
        self.calls = []
        self.first_process = True

    def fetch(self, request):
        self.calls.append((request.start_ns, request.end_ns))
        selected = [
            record
            for record in self.records
            if request.start_ns <= record.timestamp_ns <= request.end_ns
        ]
        if self.first_process:
            self.first_process = False
            # Simulate a provider process dying after returning an unordered,
            # duplicated partial response. The durable catalog keeps this prefix.
            partial = [record for record in reversed(selected) if record.timestamp_ns != 200]
            partial.append(partial[0])
            return iter(partial)
        return iter(reversed(selected))


def test_mixed_provider_response_survives_process_restart(tmp_path):
    records = tuple(
        HistoricalRecord("provider", "NFO:101", "1m", timestamp, {"close": timestamp})
        for timestamp in (100, 200, 300, 400)
    )
    request = HistoricalFetchRequest("provider", "NFO:101", "1m", 100, 400)
    plan = HistoricalSyncPlan((request,))
    catalog_path = tmp_path / "catalog.sqlite"
    jobs_path = tmp_path / "jobs.sqlite"

    provider = RestartingProvider(records)
    catalog = HistoricalCatalog(str(catalog_path))
    jobs = HistoricalJobStore(str(jobs_path))
    service = HistoricalIngestionService(catalog)
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None)

    def complete(req):
        return {
            record.timestamp_ns
            for record in catalog.records(
                source=req.source, instrument=req.instrument, timeframe=req.timeframe
            )
        } == {100, 200, 300, 400}

    first = executor.run_durable(
        provider,
        plan,
        job_store=jobs,
        job_id="restart-mixed-response",
        run_id="process-1",
        retry_attempts=1,
        should_skip=complete,
        should_accept=lambda req, result: complete(req),
    )
    assert first.failed_request_index == 0
    assert jobs.chunk_state("restart-mixed-response", 0)[0] == "recoverable"
    assert catalog.records(source="provider", instrument="NFO:101", timeframe="1m") == (
        records[0],
        records[2],
        records[3],
    )
    catalog.close()
    jobs.close()

    catalog = HistoricalCatalog(str(catalog_path))
    jobs = HistoricalJobStore(str(jobs_path))
    service = HistoricalIngestionService(catalog)
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None)

    second = executor.run_durable(
        provider,
        plan,
        job_store=jobs,
        job_id="restart-mixed-response",
        run_id="process-1",
        retry_attempts=1,
        should_skip=complete,
        should_accept=lambda req, result: complete(req),
    )

    assert second.failed_request_index is None
    assert jobs.chunk_state("restart-mixed-response", 0)[0] == "completed"
    assert jobs.pending_indices("restart-mixed-response") == ()
    assert jobs.get("restart-mixed-response").state == "completed"
    assert catalog.records(source="provider", instrument="NFO:101", timeframe="1m") == records
    assert provider.calls == [(100, 400), (100, 400)]
