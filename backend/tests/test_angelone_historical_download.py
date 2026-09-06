from datetime import datetime, timezone

from app.backtesting.angelone_historical_download import AngelOneHistoricalDownloadService
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_download_executor import DownloadExecutionResult
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalSyncResult


class FakeSource:
    def fetch(self, request):
        yield __import__("app.backtesting.historical_catalog", fromlist=["HistoricalRecord"]).HistoricalRecord(
            request.source, request.instrument, request.timeframe, request.start_ns,
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        )


def test_download_service_persists_real_provider_records():
    catalog = HistoricalCatalog()
    service = AngelOneHistoricalDownloadService(catalog, source=FakeSource())
    end = datetime(2026, 9, 6, tzinfo=timezone.utc)
    report = service.download_one_year(instrument="NSE:123:SBIN", timeframe="1d", end=end, chunk_days=365)
    assert report.completed
    assert report.catalog_count == 2
    assert catalog.watermark(source="angelone", instrument="NSE:123:SBIN", timeframe="1d") is not None
    catalog.close()
