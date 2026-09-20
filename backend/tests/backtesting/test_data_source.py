from app.backtesting.contracts import DataSourceProtocol
from app.backtesting.data_source import CatalogDataSource
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_catalog_data_source_is_protocol_compatible_and_streams():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest(
            (HistoricalRecord("src", "AAA", "tick", i, {"price": i}) for i in (3, 1, 2))
        )
        source = CatalogDataSource(catalog, source="src", instrument="AAA", timeframe="tick")
        assert isinstance(source, DataSourceProtocol)
        events = source.iter_events()
        assert iter(events) is events
        assert [record.timestamp_ns for record in events] == [1, 2, 3]
    finally:
        catalog.close()


def test_catalog_data_source_applies_range_without_materializing():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest(
            HistoricalRecord("src", "AAA", "tick", i, {"price": i}) for i in range(10)
        )
        source = CatalogDataSource(catalog, source="src", instrument="AAA", timeframe="tick")
        assert [r.timestamp_ns for r in source.iter_events(start_ns=3, end_ns=5)] == [3, 4, 5]
    finally:
        catalog.close()


def test_catalog_data_source_rejects_blank_configuration():
    catalog = HistoricalCatalog()
    try:
        for kwargs in (
            {"source": "", "timeframe": "tick"},
            {"source": "src", "instrument": "", "timeframe": "tick"},
            {"source": "src", "timeframe": ""},
        ):
            try:
                CatalogDataSource(catalog, **kwargs)
            except ValueError:
                pass
            else:
                raise AssertionError("expected ValueError")
    finally:
        catalog.close()
