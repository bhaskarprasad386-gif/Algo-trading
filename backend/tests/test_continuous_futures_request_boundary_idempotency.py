from datetime import date

from app.backtesting.continuous_futures_acquisition import (
    build_continuous_futures_acquisition_plan,
)
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.trading_calendar import TradingCalendar


def test_request_boundaries_are_contiguous_without_overlap_or_gaps():
    calendar = TradingCalendar()
    windows = (
        FNORolloverWindow(
            "AAA", "STOCK_FUTURE", "101", date(2026, 1, 29), date(2026, 1, 29)
        ),
        FNORolloverWindow(
            "AAA", "STOCK_FUTURE", "202", date(2026, 1, 30), date(2026, 2, 2)
        ),
    )
    interval_ns = 60_000_000_000
    max_request_ns = 86_400_000_000_000

    plan = build_continuous_futures_acquisition_plan(
        windows,
        source="angelone",
        timeframe="1m",
        interval_ns=interval_ns,
        calendar=calendar,
        max_request_ns=max_request_ns,
    )

    assert plan.requests

    for previous, current in zip(plan.requests, plan.requests[1:]):
        if previous.instrument == current.instrument:
            assert current.start_ns == previous.end_ns + interval_ns
        else:
            assert current.instrument != previous.instrument

    sessions = calendar.sessions_between(date(2026, 1, 29), date(2026, 2, 2))
    assert sessions
    expected_by_instrument = {
        "NFO:101": 1,
        "NFO:202": len(sessions) - 1,
    }
    actual_by_instrument = {}
    for request in plan.requests:
        actual_by_instrument[request.instrument] = (
            actual_by_instrument.get(request.instrument, 0) + 1
        )
    assert actual_by_instrument == expected_by_instrument

    for instrument in actual_by_instrument:
        instrument_requests = [
            request for request in plan.requests if request.instrument == instrument
        ]
        for previous, current in zip(instrument_requests, instrument_requests[1:]):
            assert current.start_ns == previous.end_ns + interval_ns
