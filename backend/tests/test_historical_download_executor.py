from dataclasses import dataclass

from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_sync import HistoricalSyncPlan
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalSyncResult


@dataclass
class FakeService:
    calls: int = 0
    failures: int = 0

    def sync(self, source, request):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("temporary provider failure")
        return HistoricalSyncResult(request, inserted=1, fetched=1, final_watermark_ns=request.end_ns)


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
    service = FakeService(failures=2)
    result = ResumableHistoricalExecutor(
        service, sleep=lambda _: None, collect_results=False
    ).run(object(), HistoricalSyncPlan(requests), retry_attempts=1)
    assert result.failed_request_index == 2
    assert result.completed_chunks == 2
    assert result.results == ()
