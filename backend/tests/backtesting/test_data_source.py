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


from app.backtesting.contracts import DataSourceProtocol
from app.backtesting.data_source import MultiInstrumentDataSource
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_multi_instrument_source_merges_streams_in_deterministic_order():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest(
            (
                HistoricalRecord("src", "AAA", "tick", 200, {"price": 2}),
                HistoricalRecord("src", "BBB", "tick", 100, {"price": 1}),
                HistoricalRecord("src", "AAA", "tick", 100, {"price": 3}),
                HistoricalRecord("src", "BBB", "tick", 300, {"price": 4}),
            )
        )
        source = MultiInstrumentDataSource(
            catalog, source="src", instruments=("BBB", "AAA", "AAA"), timeframe="tick"
        )
        assert isinstance(source, DataSourceProtocol)
        assert [(r.timestamp_ns, r.instrument) for r in source.iter_events()] == [
            (100, "AAA"), (100, "BBB"), (200, "AAA"), (300, "BBB")
        ]
    finally:
        catalog.close()


def test_multi_instrument_source_holds_only_one_pending_event_per_stream():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest(
            HistoricalRecord("src", instrument, "tick", timestamp, {"price": timestamp})
            for instrument in ("AAA", "BBB")
            for timestamp in range(1, 100)
        )
        source = MultiInstrumentDataSource(
            catalog, source="src", instruments=("AAA", "BBB"), timeframe="tick"
        )
        events = source.iter_events()
        assert next(events).timestamp_ns == 1
        assert next(events).timestamp_ns == 1
    finally:
        catalog.close()


def test_multi_instrument_source_applies_timestamp_range():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest(
            HistoricalRecord("src", instrument, "tick", timestamp, {})
            for instrument in ("AAA", "BBB")
            for timestamp in range(10)
        )
        source = MultiInstrumentDataSource(
            catalog, source="src", instruments=("AAA", "BBB"), timeframe="tick"
        )
        assert [(r.timestamp_ns, r.instrument) for r in source.iter_events(start_ns=3, end_ns=4)] == [
            (3, "AAA"), (3, "BBB"), (4, "AAA"), (4, "BBB")
        ]
    finally:
        catalog.close()
