from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.historical_sync import HistoricalSyncPlan


class PartialConflictProvider:
    def __init__(self, records):
        self.records = tuple(records)
        self.calls = []

    def fetch(self, request):
        self.calls.append((request.start_ns, request.end_ns))
        if len(self.calls) == 1:
            return iter(
                (
                    self.records[0],
                    self.records[1],
                    self.records[2],
                    HistoricalRecord(
                        self.records[0].source,
                        self.records[0].instrument,
                        self.records[0].timeframe,
                        self.records[0].timestamp_ns,
                        {"close": 999},
                    ),
                )
            )
        return iter(self.records)


def test_partial_committed_batch_survives_conflict_and_durable_retry(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "catalog.sqlite"))
    jobs = HistoricalJobStore(str(tmp_path / "jobs.sqlite"))
    service = HistoricalIngestionService(catalog)
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None)
    records = tuple(
        HistoricalRecord("provider", "NFO:101", "1m", ts, {"close": ts})
        for ts in (100, 200, 300, 400)
    )
    provider = PartialConflictProvider(records)
    request = HistoricalFetchRequest("provider", "NFO:101", "1m", 100, 400)
    plan = HistoricalSyncPlan((request,))

    result = executor.run_durable(
        provider,
        plan,
        job_store=jobs,
        job_id="job-1",
        run_id="run-1",
        retry_attempts=2,
        retry_delay_seconds=0,
        batch_size=2,
        should_skip=lambda current: catalog.count(
            source=current.source,
            instrument=current.instrument,
            timeframe=current.timeframe,
        ) == 4,
    )

    assert result.failed_request_index is None
    assert result.completed_chunks == 1
    assert provider.calls == [(100, 400), (100, 400)]
    assert catalog.records(
        source="provider", instrument="NFO:101", timeframe="1m"
    ) == records
    assert catalog.count(source="provider", instrument="NFO:101", timeframe="1m") == 4
    assert catalog.watermark(
        source="provider", instrument="NFO:101", timeframe="1m"
    ) == 400
    assert jobs.chunk_state("job-1", 0)[0] == "completed"
    assert jobs.pending_indices("job-1") == ()
    assert jobs.get("job-1").state == "completed"
