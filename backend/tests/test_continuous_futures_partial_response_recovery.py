from datetime import date

from app.backtesting.continuous_futures_acquisition import acquire_continuous_futures_history
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.trading_calendar import TradingCalendar


class PartialResponseSource:
    def __init__(self, records_by_instrument: dict[str, tuple[HistoricalRecord, ...]], partial_count: int) -> None:
        self.records_by_instrument = records_by_instrument
        self.partial_count = partial_count
        self.requests = []
        self.partial_served: set[str] = set()

    def fetch(self, request):
        self.requests.append(request)
        matching = [
            record
            for record in self.records_by_instrument[request.instrument]
            if request.start_ns <= record.timestamp_ns <= request.end_ns
        ]
        if request.instrument not in self.partial_served:
            self.partial_served.add(request.instrument)
            yield from matching[: self.partial_count]
            return
        yield from matching


def _session_records(calendar: TradingCalendar, instrument: str, session_date: date, interval_ns: int) -> tuple[HistoricalRecord, ...]:
    session = calendar.sessions_between(session_date, session_date)[0]
    return tuple(
        HistoricalRecord(
            source="angelone",
            instrument=instrument,
            timeframe="1m",
            timestamp_ns=timestamp_ns,
            payload={"close": 100.0},
        )
        for timestamp_ns in range(session.start_ns, session.end_ns + 1, interval_ns)
    )


def test_partial_provider_response_repairs_each_rollover_contract_independently():
    calendar = TradingCalendar()
    interval_ns = 60_000_000_000
    old_records = _session_records(calendar, "NFO:101", date(2026, 1, 29), interval_ns)
    new_records = _session_records(calendar, "NFO:202", date(2026, 1, 30), interval_ns)
    source = PartialResponseSource(
        {"NFO:101": old_records, "NFO:202": new_records},
        partial_count=3,
    )
    catalog = HistoricalCatalog()
    windows = (
        FNORolloverWindow(
            "AAA", "STOCK_FUTURE", "101", date(2026, 1, 29), date(2026, 1, 29)
        ),
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
    assert len(source.requests) == 4

    old_session = calendar.sessions_between(date(2026, 1, 29), date(2026, 1, 29))[0]
    new_session = calendar.sessions_between(date(2026, 1, 30), date(2026, 1, 30))[0]

    assert source.requests[0].instrument == "NFO:101"
    assert source.requests[0].start_ns == old_session.start_ns
    assert source.requests[0].end_ns == old_session.end_ns
    assert source.requests[1].instrument == "NFO:101"
    assert source.requests[1].start_ns == old_session.start_ns + 3 * interval_ns
    assert source.requests[1].end_ns == old_session.end_ns

    assert source.requests[2].instrument == "NFO:202"
    assert source.requests[2].start_ns == new_session.start_ns
    assert source.requests[2].end_ns == new_session.end_ns
    assert source.requests[3].instrument == "NFO:202"
    assert source.requests[3].start_ns == new_session.start_ns + 3 * interval_ns
    assert source.requests[3].end_ns == new_session.end_ns

    assert catalog.count(source="angelone", instrument="NFO:101", timeframe="1m") == len(old_records)
    assert catalog.count(source="angelone", instrument="NFO:202", timeframe="1m") == len(new_records)
