from datetime import date

from app.backtesting.continuous_futures_acquisition import acquire_continuous_futures_history
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.trading_calendar import TradingCalendar


class PartialResponseSource:
    def __init__(self, records: tuple[HistoricalRecord, ...], partial_count: int) -> None:
        self.records = records
        self.partial_count = partial_count
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        matching = [
            record
            for record in self.records
            if request.start_ns <= record.timestamp_ns <= request.end_ns
        ]
        if len(self.requests) == 1:
            yield from matching[: self.partial_count]
            return
        yield from matching


def test_partial_provider_response_retries_only_missing_tail_for_rollover_session():
    calendar = TradingCalendar()
    session = calendar.sessions_between(date(2026, 1, 30), date(2026, 1, 30))[0]
    interval_ns = 60_000_000_000
    records = tuple(
        HistoricalRecord(
            source="angelone",
            instrument="NFO:202",
            timeframe="1m",
            timestamp_ns=timestamp_ns,
            payload={"close": 100.0},
        )
        for timestamp_ns in range(session.start_ns, session.end_ns + 1, interval_ns)
    )
    source = PartialResponseSource(records, partial_count=3)
    catalog = HistoricalCatalog()
    windows = (
        FNORolloverWindow(
            "AAA", "STOCK_FUTURE", "202", date(2026, 1, 30), date(2026, 1, 30)
        ),
    )

    report = acquire_continuous_futures_history(
        catalog,
        source,
        windows,
        source_name="angelone",
        timeframe="1m",
        interval_ns=interval_ns,
        calendar=calendar,
        max_request_ns=86_400_000_000_000,
    )

    assert report.completed
    assert len(source.requests) == 2
    assert source.requests[0].instrument == "NFO:202"
    assert source.requests[0].start_ns == session.start_ns
    assert source.requests[0].end_ns == session.end_ns
    assert source.requests[1].instrument == "NFO:202"
    assert source.requests[1].start_ns == session.start_ns + 3 * interval_ns
    assert source.requests[1].end_ns == session.end_ns
    assert catalog.count(source="angelone", instrument="NFO:202", timeframe="1m") == len(records)
