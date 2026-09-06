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
