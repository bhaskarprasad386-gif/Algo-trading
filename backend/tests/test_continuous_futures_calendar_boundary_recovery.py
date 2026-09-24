from __future__ import annotations

from datetime import date, datetime, time, timezone

from app.backtesting.continuous_futures_acquisition import acquire_continuous_futures_history
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.trading_calendar import TradingCalendar

INTERVAL_NS = 60 * 1_000_000_000


def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=__import__('zoneinfo').ZoneInfo('Asia/Kolkata')).timestamp() * 1_000_000_000)


class CalendarBoundarySource:
    source_name = "fake"

    def __init__(self) -> None:
        self.requests: list[HistoricalFetchRequest] = []
        self.partial_monday = True
        self.fail_after_partial = False

    def fetch(self, request: HistoricalFetchRequest):
        self.requests.append(request)
        # Friday is complete. Monday deliberately omits 09:17; the weekend and
        # an explicitly closed Tuesday must never become requested data ranges.
        monday = date(2026, 1, 12)
        if self.fail_after_partial and request.start_ns == _ns(monday, time(9, 17)):
            raise RuntimeError("simulated durable interruption on remaining Monday gap")
        if request.start_ns == _ns(monday, time(9, 15)) and self.partial_monday:
            for at in (time(9, 15), time(9, 16), time(9, 18)):
                yield HistoricalRecord(
                    request.source,
                    request.instrument,
                    request.timeframe,
                    _ns(monday, at),
                    {"close": 100.0},
                )
            if self.fail_after_partial:
                raise RuntimeError("simulated durable interruption after partial Monday response")
            return
        for timestamp in range(request.start_ns, request.end_ns + 1, INTERVAL_NS):
            yield HistoricalRecord(request.source, request.instrument, request.timeframe, timestamp, {"close": 100.0})


def test_calendar_boundary_recovery_skips_weekend_and_closed_date(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(
        session_open=time(9, 15),
        session_close=time(9, 18),
        closed_dates=frozenset({date(2026, 1, 13)}),
    )
    window = FNORolloverWindow(
        "ABC",
        "STOCK_FUTURE",
        "JAN",
        date(2026, 1, 9),
        date(2026, 1, 13),
    )
    source = CalendarBoundarySource()

    result = acquire_continuous_futures_history(
        catalog,
        source,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="calendar-boundary-job",
        run_id="run-1",
    )

    assert result.completed
    assert store.get("calendar-boundary-job").state == "completed"

    # Only Friday and Monday sessions are requested; Saturday, Sunday, and the
    # explicitly closed Tuesday are absent from all provider requests.
    requested_dates = {datetime.fromtimestamp(r.start_ns / 1_000_000_000, tz=timezone.utc).date() for r in source.requests}
    assert requested_dates == {date(2026, 1, 9), date(2026, 1, 12)}
    assert all(
        datetime.fromtimestamp(r.start_ns / 1_000_000_000, tz=timezone.utc).weekday() < 5
        for r in source.requests
    )
    assert all(
        datetime.fromtimestamp(r.start_ns / 1_000_000_000, tz=timezone.utc).date() != date(2026, 1, 13)
        for r in source.requests
    )

    # The acquisition layer repairs the partial Monday response in the same run.
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 8
    monday_gaps = catalog.session_gaps(
        source="fake",
        instrument="NFO:JAN",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        start_date=date(2026, 1, 12),
        end_date=date(2026, 1, 12),
    )
    assert monday_gaps == ()


def test_calendar_boundary_resume_repairs_only_monday_gap(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    store = HistoricalJobStore(tmp_path / "jobs.sqlite")
    calendar = TradingCalendar(
        session_open=time(9, 15),
        session_close=time(9, 18),
        closed_dates=frozenset({date(2026, 1, 13)}),
    )
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 9), date(2026, 1, 13))
    source = CalendarBoundarySource()

    source.fail_after_partial = True
    first = acquire_continuous_futures_history(
        catalog,
        source,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="calendar-boundary-resume-job",
        run_id="run-1",
    )
    assert not first.completed
    assert store.get("calendar-boundary-resume-job").state == "progress"
    fingerprint = store.get("calendar-boundary-resume-job").plan_fingerprint

    source.partial_monday = False
    source.fail_after_partial = False
    before = len(source.requests)
    second = acquire_continuous_futures_history(
        catalog,
        source,
        [window],
        source_name="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
        job_store=store,
        job_id="calendar-boundary-resume-job",
        run_id="run-1",
    )

    assert second.completed
    assert store.get("calendar-boundary-resume-job").state == "completed"
    assert store.get("calendar-boundary-resume-job").plan_fingerprint == fingerprint
    resumed = source.requests[before:]
    assert len(resumed) == 1
    assert resumed[0].start_ns == _ns(date(2026, 1, 12), time(9, 17))
    assert resumed[0].end_ns == _ns(date(2026, 1, 12), time(9, 17))
    assert catalog.count(source="fake", instrument="NFO:JAN", timeframe="1m") == 8
