from pathlib import Path

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.historical_sync import build_chunked_plan
from app.backtesting.provider_retry import ProviderRetryPolicy


class Http429Error(Exception):
    status_code = 429


class RetryThenSuccessSource:
    def __init__(self):
        self.calls = []
        self.failed_once = False

    def fetch(self, request):
        self.calls.append((request.start_ns, request.end_ns))
        if not self.failed_once:
            self.failed_once = True
            raise Http429Error("provider rate limit")
        yield HistoricalRecord(
            request.source,
            request.instrument,
            request.timeframe,
            request.start_ns,
            {"close": 100.0},
        )


def test_durable_retry_completes_once_and_resume_skips_completed_chunks(tmp_path: Path):
    history = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(history)
    executor = ResumableHistoricalExecutor(ingestion, sleep=lambda _: None, collect_results=False)
    source = RetryThenSuccessSource()
    plan = build_chunked_plan(
        source="angelone",
        instrument="NSE:3045:SBIN",
        timeframe="1m",
        start_ns=0,
        end_ns=119,
        chunk_ns=60,
    )
    job_store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    sleeps = []
    policy = ProviderRetryPolicy(
        min_interval_seconds=0.5,
        base_delay_seconds=0,
        max_delay_seconds=0,
        jitter_ratio=0,
        sleeper=lambda seconds: sleeps.append(seconds),
    )

    first = executor.run_durable(
        source,
        plan,
        job_store=job_store,
        job_id="job-1",
        run_id="run-1",
        retry_attempts=2,
        retry_policy=policy,
    )

    assert first.failed_request_index is None
    assert first.completed_chunks == 2
    assert source.calls == [(0, 59), (0, 59), (60, 119)]
    assert len(sleeps) == 2
    assert all(0 < delay <= 0.5 for delay in sleeps)
    assert job_store.chunk_state("job-1", 0)[:2] == ("completed", 1)
    assert job_store.chunk_state("job-1", 1)[:2] == ("completed", 1)
    assert job_store.get("job-1").state == "completed"

    second = executor.run_durable(
        source,
        plan,
        job_store=job_store,
        job_id="job-1",
        run_id="run-1",
        retry_attempts=2,
        retry_policy=policy,
    )

    assert second.failed_request_index is None
    assert second.completed_chunks == 0
    assert second.skipped_request_indices == (0, 1)
    assert source.calls == [(0, 59), (0, 59), (60, 119)]
    assert len(sleeps) >= 2
    assert job_store.get("job-1").state == "completed"
    job_store.close()
    history.close()
