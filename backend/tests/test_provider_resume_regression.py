from datetime import datetime, timezone

import pytest

from backend.app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from backend.app.backtesting.historical_catalog import HistoricalCatalog
from backend.app.backtesting.historical_download_status import HistoricalDownloadStatusStore


class _Source:
    source_name = "other-provider"

    def fetch(self, request):
        raise AssertionError("provider mismatch must fail before fetch")


def test_resume_rejects_provider_mismatch(tmp_path):
    store = HistoricalDownloadStatusStore(str(tmp_path / "status.sqlite"))
    store.create_job(
        job_id="job-1", mode="BOTH", timeframe="1m", spot_instrument="NSE:1:ABC",
        exchange="NSE", underlying="ABC", start_ns=1, end_ns=2,
        requested_chunks=1, source="angelone", reset_existing=True,
    )
    catalog = HistoricalCatalog(str(tmp_path / "catalog.sqlite"))
    service = CashFutureHistoricalDownloadService(catalog, object(), source=_Source(), status_store=store)
    with pytest.raises(ValueError, match="provider mismatch"):
        service.run(
            spot_instrument="NSE:1:ABC", exchange="NSE", underlying="ABC",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            resume=True, job_id="job-1",
        )
