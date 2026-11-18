from dataclasses import dataclass

from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalSyncResult
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.historical_sync import HistoricalSyncPlan
from app.backtesting.provider_retry import ProviderRetryPolicy


class HttpError(RuntimeError):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@dataclass
class RetryService:
    failures_before_success: int = 1

    def __post_init__(self):
        self.calls = 0

    def sync_streaming(self, source, request, *, batch_size, on_batch=None):
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise HttpError(503)
        return HistoricalSyncResult(request, inserted=1, fetched=1, final_watermark_ns=request.end_ns)


def _plan() -> HistoricalSyncPlan:
    return HistoricalSyncPlan(
        (
            HistoricalFetchRequest("provider", "SBIN", "1m", 1, 60),
        )
    )


def test_durable_retry_completes_once_and_resume_skips_completed_chunk():
    store = HistoricalJobStore(":memory:")
    service = RetryService(failures_before_success=1)
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None, collect_results=False)
    sleeps = []
    policy = ProviderRetryPolicy(
        min_interval_seconds=0.5,
        base_delay_seconds=0.25,
        jitter_ratio=0,
        sleeper=lambda seconds: sleeps.append(seconds),
    )

    first = executor.run_durable(
        object(),
        _plan(),
        job_store=store,
        job_id="job-1",
        run_id="run-1",
        retry_attempts=2,
        retry_policy=policy,
    )

    assert first.failed_request_index is None
    assert first.completed_chunks == 1
    assert service.calls == 2
    assert store.chunk_state("job-1", 0) == ("completed", 1, None)
    assert store.get("job-1").state == "completed"
    assert len(sleeps) == 2
    assert sleeps[0] == 0.25
    assert 0 < sleeps[1] <= 0.5

    resumed = executor.run_durable(
        object(),
        _plan(),
        job_store=store,
        job_id="job-1",
        run_id="run-1",
        retry_attempts=2,
        retry_policy=policy,
    )

    assert resumed.failed_request_index is None
    assert resumed.completed_chunks == 0
    assert resumed.skipped_chunks == 1
    assert service.calls == 2
    assert store.chunk_state("job-1", 0) == ("completed", 1, None)


def test_durable_recovery_requeues_interrupted_running_chunk():
    store = HistoricalJobStore(":memory:")
    plan = _plan()
    metadata = tuple(ResumableHistoricalExecutor._request_metadata(r) for r in plan.requests)
    store.create(
        job_id="job-crash",
        run_id="run-crash",
        plan_fingerprint=store.fingerprint(metadata),
        total_chunks=1,
    )
    store.start_chunk("job-crash", 0)

    assert store.chunk_state("job-crash", 0)[0] == "running"
    assert store.recover_running_chunks("job-crash") == (0,)
    assert store.chunk_state("job-crash", 0)[0] == "recoverable"
    assert store.pending_indices("job-crash") == (0,)

    service = RetryService(failures_before_success=0)
    executor = ResumableHistoricalExecutor(service, sleep=lambda _: None, collect_results=False)
    result = executor.run_durable(
        object(),
        plan,
        job_store=store,
        job_id="job-crash",
        run_id="run-crash",
        retry_attempts=1,
    )

    assert result.failed_request_index is None
    assert result.completed_chunks == 1
    assert service.calls == 1
    assert store.chunk_state("job-crash", 0) == ("completed", 2, None)
    assert store.get("job-crash").state == "completed"
