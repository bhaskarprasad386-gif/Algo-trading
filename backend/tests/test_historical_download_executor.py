from dataclasses import dataclass

from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_sync import HistoricalSyncPlan
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalSyncResult
from app.backtesting.historical_job_store import HistoricalJobStore


@dataclass
class FakeService:
    calls: int = 0
    failures: int = 0
    batch_callbacks: int = 0

    def sync_streaming(self, source, request, *, batch_size=1024, on_batch=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("temporary provider failure")
        if on_batch is not None:
            self.batch_callbacks += 1
            on_batch(1, 1)
        return HistoricalSyncResult(request, inserted=1, fetched=1, final_watermark_ns=request.end_ns)

    def sync(self, source, request):
        return self.sync_streaming(source, request)


@dataclass
class FailOnCallService:
    calls: int = 0
    fail_on_call: int = 0

    def sync_streaming(self, source, request, *, batch_size=1024, on_batch=None):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("permanent provider failure")
        return HistoricalSyncResult(request, inserted=1, fetched=1, final_watermark_ns=request.end_ns)

    def sync(self, source, request):
        return self.sync_streaming(source, request)


def test_retries_failed_chunk_then_continues():
    requests = tuple(HistoricalFetchRequest("x", "i", "1m", n, n) for n in range(3))
    service = FakeService(failures=1)
    result = ResumableHistoricalExecutor(service, sleep=lambda _: None).run(
        object(), HistoricalSyncPlan(requests), retry_attempts=2
    )
    assert result.failed_request_index is None
    assert result.completed_chunks == 3
    assert service.calls == 4


def test_failure_reports_exact_chunk_and_stops_without_fake_data():
    requests = tuple(HistoricalFetchRequest("x", "i", "1m", n, n) for n in range(3))
    service = FakeService(failures=5)
    result = ResumableHistoricalExecutor(service, sleep=lambda _: None).run(
        object(), HistoricalSyncPlan(requests), retry_attempts=2
    )
    assert result.failed_request_index == 0
    assert result.completed_chunks == 0


def test_incomplete_chunk_is_retried_and_not_marked_complete():
    requests = (HistoricalFetchRequest("x", "i", "1m", 0, 0),)
    service = FakeService()
    accepted = iter((False, True))
    result = ResumableHistoricalExecutor(service, sleep=lambda _: None).run(
        object(), HistoricalSyncPlan(requests), retry_attempts=2,
        should_accept=lambda _request, _result: next(accepted),
    )
    assert result.failed_request_index is None
    assert result.completed_chunks == 1
    assert service.calls == 2


def test_permanently_incomplete_chunk_is_failure():
    requests = (HistoricalFetchRequest("x", "i", "1m", 0, 0),)
    service = FakeService()
    result = ResumableHistoricalExecutor(service, sleep=lambda _: None).run(
        object(), HistoricalSyncPlan(requests), retry_attempts=2,
        should_accept=lambda _request, _result: False,
    )
    assert result.failed_request_index == 0
    assert result.completed_chunks == 0


def test_bounded_mode_does_not_retain_all_completed_results():
    requests = tuple(HistoricalFetchRequest("x", "i", "1m", n, n) for n in range(1000))
    service = FakeService()
    observed = []
    result = ResumableHistoricalExecutor(
        service, sleep=lambda _: None, collect_results=False
    ).run(
        object(),
        HistoricalSyncPlan(requests),
        retry_attempts=1,
        on_chunk_complete=lambda index, _request, chunk_result, _attempt: observed.append(
            (index, chunk_result.inserted)
        ),
    )
    assert result.failed_request_index is None
    assert result.completed_chunks == 1000
    assert result.results == ()
    assert len(observed) == 1000
    assert service.calls == 1000


def test_bounded_mode_preserves_completed_count_when_later_chunk_fails():
    requests = tuple(HistoricalFetchRequest("x", "i", "1m", n, n) for n in range(5))
    service = FailOnCallService(fail_on_call=3)
    result = ResumableHistoricalExecutor(
        service, sleep=lambda _: None, collect_results=False
    ).run(object(), HistoricalSyncPlan(requests), retry_attempts=1)
    assert result.failed_request_index == 2
    assert result.completed_chunks == 2
    assert result.results == ()


def test_executor_forwards_bounded_batch_progress_without_raw_rows():
    request = HistoricalFetchRequest("x", "i", "1m", 0, 0)
    service = FakeService()
    progress = []
    result = ResumableHistoricalExecutor(
        service, sleep=lambda _: None, collect_results=False
    ).run(
        object(),
        HistoricalSyncPlan((request,)),
        retry_attempts=1,
        batch_size=7,
        on_batch=lambda inserted, fetched: progress.append((inserted, fetched)),
    )
    assert result.results == ()
    assert result.completed_chunks == 1
    assert service.batch_callbacks == 1
    assert progress == [(1, 1)]


def test_durable_executor_resumes_only_recoverable_chunks(tmp_path):
    requests = tuple(HistoricalFetchRequest("x", "i", "1m", n, n) for n in range(3))
    plan = HistoricalSyncPlan(requests)
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))

    first = FailOnCallService(fail_on_call=2)
    result1 = ResumableHistoricalExecutor(first, sleep=lambda _: None, collect_results=False).run_durable(
        object(), plan, job_store=store, job_id="job-1", run_id="run-1", retry_attempts=1
    )
    assert result1.failed_request_index == 1
    assert result1.completed_chunks == 1
    assert store.pending_indices("job-1") == (1, 2)

    second = FakeService()
    result2 = ResumableHistoricalExecutor(second, sleep=lambda _: None, collect_results=False).run_durable(
        object(), plan, job_store=store, job_id="job-1", run_id="run-1", retry_attempts=1
    )
    assert result2.failed_request_index is None
    assert result2.completed_chunks == 2
    assert result2.skipped_request_indices == (0,)
    assert second.calls == 2
    assert store.pending_indices("job-1") == ()
    assert store.get("job-1").state == "completed"


def test_durable_executor_rejects_plan_or_run_mismatch(tmp_path):
    requests = (HistoricalFetchRequest("x", "i", "1m", 0, 0),)
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    executor = ResumableHistoricalExecutor(FakeService(), sleep=lambda _: None)
    executor.run_durable(object(), HistoricalSyncPlan(requests), job_store=store, job_id="job-1", run_id="run-1")

    try:
        executor.run_durable(
            object(),
            HistoricalSyncPlan((HistoricalFetchRequest("x", "i", "1m", 1, 1),)),
            job_store=store,
            job_id="job-1",
            run_id="run-1",
        )
    except ValueError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("expected plan mismatch")

    try:
        executor.run_durable(object(), HistoricalSyncPlan(requests), job_store=store, job_id="job-1", run_id="run-2")
    except ValueError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("expected run mismatch")
