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

    sessions = calendar.sessions_between(date(2026, 1, 29), date(2026, 2, 2))
    assert sessions

    for instrument in {request.instrument for request in plan.requests}:
        instrument_requests = [request for request in plan.requests if request.instrument == instrument]
        for previous, current in zip(instrument_requests, instrument_requests[1:]):
            previous_session = next(session for session in sessions if session.start_ns <= previous.start_ns <= session.end_ns)
            current_session = next(session for session in sessions if session.start_ns <= current.start_ns <= session.end_ns)
            if previous_session == current_session:
                assert current.start_ns == previous.end_ns + interval_ns
            else:
                assert current_session.start_ns > previous_session.end_ns

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
