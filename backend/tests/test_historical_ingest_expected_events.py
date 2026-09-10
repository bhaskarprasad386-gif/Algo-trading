from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService


class FakeEventSource:
    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        for timestamp_ns in (request.start_ns, request.end_ns):
            yield HistoricalRecord(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
                timestamp_ns=timestamp_ns,
                payload={"event": timestamp_ns},
            )


def test_repair_expected_events_fetches_only_missing_authoritative_ranges():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest((HistoricalRecord("events", "NSE:1:TEST", "tick", 100, {"event": 100}),))
        source = FakeEventSource()
        service = HistoricalIngestionService(catalog)

        results = service.repair_expected_events(
            source,
            source="events",
            instrument="NSE:1:TEST",
            timeframe="tick",
            expected_timestamps=(100, 150, 180, 300),
            max_request_ns=100,
            batch_size=1,
        )

        assert [(item.start_ns, item.end_ns) for item in source.requests] == [
            (150, 180),
            (300, 300),
        ]
        assert sum(item.inserted for item in results) == 3
        assert catalog.timestamps(
            source="events",
            instrument="NSE:1:TEST",
            timeframe="tick",
            start_ns=0,
            end_ns=1_000,
        ) == (100, 150, 180, 300)
    finally:
        catalog.close()


def test_repair_expected_events_is_noop_when_every_expected_event_exists():
    catalog = HistoricalCatalog()
    try:
        catalog.ingest((
            HistoricalRecord("events", "NSE:1:TEST", "tick", 100, {"event": 100}),
            HistoricalRecord("events", "NSE:1:TEST", "tick", 300, {"event": 300}),
        ))
        source = FakeEventSource()
        service = HistoricalIngestionService(catalog)

        assert service.repair_expected_events(
            source,
            source="events",
            instrument="NSE:1:TEST",
            timeframe="tick",
            expected_timestamps=(100, 300),
            max_request_ns=100,
        ) == ()
        assert source.requests == []
    finally:
        catalog.close()
