from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService


class _Provider:
    def __init__(self, records):
        self.records = tuple(records)
        self.requests = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        return tuple(
            record
            for record in self.records
            if request.start_ns <= record.timestamp_ns <= request.end_ns
        )


def test_repair_expected_events_fetches_and_durably_ingests_only_missing_events(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "history.db"))
    try:
        catalog.ingest([
            HistoricalRecord("provider", "NSE:1:TEST", "tick", 10, {"price": 100.0}),
        ])
        provider = _Provider([
            HistoricalRecord("provider", "NSE:1:TEST", "tick", 20, {"price": 101.0}),
            HistoricalRecord("provider", "NSE:1:TEST", "tick", 25, {"price": 101.5}),
            HistoricalRecord("provider", "NSE:1:TEST", "tick", 40, {"price": 102.0}),
        ])

        results = HistoricalIngestionService(catalog).repair_expected_events(
            provider,
            source="provider",
            instrument="NSE:1:TEST",
            timeframe="tick",
            expected_timestamps=[10, 20, 25, 40],
            max_request_ns=10,
        )

        assert [(request.start_ns, request.end_ns) for request in provider.requests] == [
            (20, 25),
            (40, 40),
        ]
        assert [result.fetched for result in results] == [2, 1]
        assert catalog.timestamps(
            source="provider",
            instrument="NSE:1:TEST",
            timeframe="tick",
            start_ns=10,
            end_ns=40,
        ) == (10, 20, 25, 40)
    finally:
        catalog.close()


def test_expected_event_repair_reports_unresolved_provider_misses_and_is_resumable(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "history.db"))
    try:
        provider = _Provider([
            HistoricalRecord("provider", "NSE:1:TEST", "tick", 20, {"price": 101.0}),
            HistoricalRecord("provider", "NSE:1:TEST", "tick", 40, {"price": 102.0}),
        ])
        service = HistoricalIngestionService(catalog)

        first = service.repair_expected_events_with_status(
            provider,
            source="provider",
            instrument="NSE:1:TEST",
            timeframe="tick",
            expected_timestamps=[10, 20, 30, 40],
            max_request_ns=10,
        )
        assert first.remaining_missing_timestamps == (10, 30)

        provider.records = provider.records + (
            HistoricalRecord("provider", "NSE:1:TEST", "tick", 10, {"price": 100.0}),
            HistoricalRecord("provider", "NSE:1:TEST", "tick", 30, {"price": 101.5}),
        )
        second = service.repair_expected_events_with_status(
            provider,
            source="provider",
            instrument="NSE:1:TEST",
            timeframe="tick",
            expected_timestamps=[10, 20, 30, 40],
            max_request_ns=10,
        )
        assert second.remaining_missing_timestamps == ()
        assert [(request.start_ns, request.end_ns) for request in provider.requests] == [
            (10, 10), (20, 20), (30, 30), (40, 40), (10, 10), (30, 30)
        ]
    finally:
        catalog.close()
