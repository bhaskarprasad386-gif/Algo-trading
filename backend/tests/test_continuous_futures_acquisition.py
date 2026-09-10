from __future__ import annotations

from datetime import date, time, datetime, timezone

from app.backtesting.continuous_futures_acquisition import (
    acquire_continuous_futures_history,
    build_continuous_futures_acquisition_plan,
)
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.trading_calendar import TradingCalendar


INTERVAL_NS = 60 * 1_000_000_000


class FakeHistoricalSource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 100.0})
        if request.end_ns != request.start_ns:
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.end_ns, {"close": 101.0})


def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_plan_never_crosses_rollover_windows_or_closed_days():
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 16))
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 5), date(2026, 1, 5)),
    )

    plan = build_continuous_futures_acquisition_plan(
        windows,
        source="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
    )

    assert [(r.instrument, r.start_ns, r.end_ns) for r in plan.requests] == [
        ("NFO:JAN", _ns(date(2026, 1, 2), time(9, 15)), _ns(date(2026, 1, 2), time(9, 16))),
        ("NFO:FEB", _ns(date(2026, 1, 5), time(9, 15)), _ns(date(2026, 1, 5), time(9, 16))),
    ]


def test_acquisition_persists_and_second_run_skips_complete_chunks():
    catalog = HistoricalCatalog()
    source = FakeHistoricalSource()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 16))
    windows = (FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)),)

    first = acquire_continuous_futures_history(
        catalog,
        source,
        windows,
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
    )
    assert first.completed
    assert first.execution.completed_chunks == 1
    assert first.execution.skipped_chunks == 0
    assert len(source.requests) == 1
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 2

    second = acquire_continuous_futures_history(
        catalog,
        source,
        windows,
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
    )
    assert second.completed
    assert second.execution.completed_chunks == 0
    assert second.execution.skipped_chunks == 1
    assert len(source.requests) == 1
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 2
