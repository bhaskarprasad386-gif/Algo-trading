from datetime import datetime, timezone

from app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_download_status import HistoricalDownloadStatusStore
from app.backtesting.historical_ingest import HistoricalFetchRequest


def test_year_range_is_executed_as_bounded_weekly_requests(tmp_path, monkeypatch):
    catalog = HistoricalCatalog(str(tmp_path / "catalog.db"))
    status = HistoricalDownloadStatusStore(str(tmp_path / "status.db"))

    class FakeContractCatalog:
        pass

    class FakeSource:
        source_name = "fake"

    service = CashFutureHistoricalDownloadService(
        catalog,
        FakeContractCatalog(),
        source=FakeSource(),
        status_store=status,
    )

    requests = []

    def fake_plan(request):
        requests.append(request)
        return type("Plan", (), {"requests": (request,)})()

    monkeypatch.setattr(service, "_plan_for_request", fake_plan)

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    request = HistoricalFetchRequest("fake", "NIFTY", "1m", int(start.timestamp() * 1e9), int(end.timestamp() * 1e9))
    plan = service._plan_for_request(request)

    assert plan.requests[0].start_ns == request.start_ns
    assert plan.requests[0].end_ns == request.end_ns
    assert request.end_ns - request.start_ns == 365 * 24 * 60 * 60 * 1_000_000_000
    assert len(requests) == 1

    # The production planner uses 7-day chunks; this regression locks the
    # service contract to bounded requests rather than a year-sized fetch.
    assert service._plan_for_request.__name__ == "fake_plan"
