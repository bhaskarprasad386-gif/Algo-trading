from datetime import date

import pytest

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_sync import build_session_gap_plan
from app.backtesting.trading_calendar import TradingCalendar


NS_PER_MINUTE = 60 * 1_000_000_000


def _ts(day: date, minute: int) -> int:
    return TradingCalendar().session_for(day).start_ns + minute * NS_PER_MINUTE


def test_session_gap_plan_does_not_bridge_overnight_or_weekend():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar()
    day1 = date(2026, 9, 4)  # Friday
    day2 = date(2026, 9, 7)  # Monday
    catalog.ingest(
        [
            HistoricalRecord("cash", "ABC", "1m", _ts(day1, 0), {"close": 1}),
            HistoricalRecord("cash", "ABC", "1m", _ts(day1, 2), {"close": 2}),
            HistoricalRecord("cash", "ABC", "1m", _ts(day2, 0), {"close": 3}),
        ]
    )

    plan = build_session_gap_plan(
        catalog,
        calendar,
        source="cash",
        instrument="ABC",
        timeframe="1m",
        interval_ns=NS_PER_MINUTE,
        start_date=day1,
        end_date=day2,
        max_request_ns=10 * NS_PER_MINUTE,
    )

    assert [(r.start_ns, r.end_ns) for r in plan.requests] == [
        (_ts(day1, 1), _ts(day1, 1))
    ]


def test_session_gap_plan_bounds_same_session_repairs():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar()
    day = date(2026, 9, 7)
    catalog.ingest(
        [
            HistoricalRecord("cash", "ABC", "1m", _ts(day, 0), {"close": 1}),
            HistoricalRecord("cash", "ABC", "1m", _ts(day, 6), {"close": 2}),
        ]
    )

    plan = build_session_gap_plan(
        catalog,
        calendar,
        source="cash",
        instrument="ABC",
        timeframe="1m",
        interval_ns=NS_PER_MINUTE,
        start_date=day,
        end_date=day,
        max_request_ns=2 * NS_PER_MINUTE,
    )

    assert [(r.start_ns, r.end_ns) for r in plan.requests] == [
        (_ts(day, 1), _ts(day, 2)),
        (_ts(day, 3), _ts(day, 4)),
        (_ts(day, 5), _ts(day, 5)),
    ]


def test_session_gap_plan_does_not_invent_gap_for_single_record_session():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar()
    day = date(2026, 9, 7)
    catalog.ingest([HistoricalRecord("cash", "ABC", "1m", _ts(day, 0), {"close": 1})])

    plan = build_session_gap_plan(
        catalog,
        calendar,
        source="cash",
        instrument="ABC",
        timeframe="1m",
        interval_ns=NS_PER_MINUTE,
        start_date=day,
        end_date=day,
        max_request_ns=10 * NS_PER_MINUTE,
    )

    assert plan.requests == ()


def test_session_gap_plan_validates_positive_bounds():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar()
    day = date(2026, 9, 7)
    with pytest.raises(ValueError):
        build_session_gap_plan(
            catalog,
            calendar,
            source="cash",
            instrument="ABC",
            timeframe="1m",
            interval_ns=0,
            start_date=day,
            end_date=day,
            max_request_ns=1,
        )
    with pytest.raises(ValueError):
        build_session_gap_plan(
            catalog,
            calendar,
            source="cash",
            instrument="ABC",
            timeframe="1m",
            interval_ns=NS_PER_MINUTE,
            start_date=day,
            end_date=day,
            max_request_ns=0,
        )
