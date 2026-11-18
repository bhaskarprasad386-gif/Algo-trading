import pytest

from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.backtesting.historical_provider_capabilities import capabilities


class _UnsupportedSource:
    capabilities = capabilities("example", ("1m",))

    def fetch(self, request):
        raise AssertionError("fetch must not be called")


class _SupportedSource:
    capabilities = capabilities("example", ("1m", "1ms"))

    def fetch(self, request):
        return ()


def test_ingestion_rejects_unsupported_declared_provider_resolution(tmp_path):
    service = HistoricalIngestionService(HistoricalCatalog(tmp_path / "catalog.db"))
    request = HistoricalFetchRequest("example", "ABC", "1ms", 1, 2)

    with pytest.raises(ValueError, match="does not natively support timeframe"):
        service.sync(_UnsupportedSource(), request)


def test_ingestion_allows_declared_high_resolution_provider(tmp_path):
    service = HistoricalIngestionService(HistoricalCatalog(tmp_path / "catalog.db"))
    request = HistoricalFetchRequest("example", "ABC", "1ms", 1, 2)

    result = service.sync(_SupportedSource(), request)

    assert result.requested == request
    assert result.fetched == 0
