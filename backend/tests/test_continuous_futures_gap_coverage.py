from datetime import date, time, datetime, timezone

from app.backtesting.continuous_futures_acquisition import build_continuous_futures_gap_plan
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.trading_calendar import TradingCalendar

INTERVAL_NS = 60 * 1_000_000_000


def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_cash_future_gap_plan_repairs_leading_trailing_and_empty_sessions(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 18))
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 6))

    friday = calendar.sessions_between(date(2026, 1, 2), date(2026, 1, 2))[0]
    tuesday = calendar.sessions_between(date(2026, 1, 6), date(2026, 1, 6))[0]
    catalog.ingest(
        HistoricalRecord("fake", "NFO:JAN", "1m", timestamp, {"close": 100.0})
        for timestamp in (
            friday.start_ns + INTERVAL_NS,
            friday.start_ns + 2 * INTERVAL_NS,
            tuesday.start_ns,
            tuesday.start_ns + INTERVAL_NS,
            tuesday.start_ns + 2 * INTERVAL_NS,
            tuesday.end_ns,
        )
    )

    plan = build_continuous_futures_gap_plan(
        catalog,
        [window],
        source="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
    )

    assert [(r.instrument, r.start_ns, r.end_ns) for r in plan.requests] == [
        ("NFO:JAN", friday.start_ns, friday.start_ns),
        ("NFO:JAN", friday.end_ns, friday.end_ns),
        ("NFO:JAN", _ns(date(2026, 1, 5), time(9, 15)), _ns(date(2026, 1, 5), time(9, 18))),
    ]


def test_cash_future_gap_plan_never_crosses_closed_day_or_rollover_boundary(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 16))
    jan = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2))
    feb = FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 5), date(2026, 1, 5))

    plan = build_continuous_futures_gap_plan(
        catalog,
        [jan, feb],
        source="fake",
        timeframe="1m",
        interval_ns=INTERVAL_NS,
        calendar=calendar,
        max_request_ns=10 * INTERVAL_NS,
    )

    assert len(plan.requests) == 2
    assert plan.requests[0].instrument == "NFO:JAN"
    assert plan.requests[1].instrument == "NFO:FEB"
    assert plan.requests[0].end_ns < plan.requests[1].start_ns
