from datetime import date

import pytest

from app.backtesting.continuous_futures_acquisition import build_continuous_futures_acquisition_plan
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.trading_calendar import TradingCalendar


def test_rollover_transition_keeps_old_and_new_contract_session_coverage_separate():
    calendar = TradingCalendar()
    windows = (
        FNORolloverWindow(
            underlying="AAA",
            instrument_type="STOCK_FUTURE",
            contract_token="101",
            start_date=date(2026, 1, 28),
            end_date=date(2026, 1, 29),
        ),
        FNORolloverWindow(
            underlying="AAA",
            instrument_type="STOCK_FUTURE",
            contract_token="202",
            start_date=date(2026, 1, 30),
            end_date=date(2026, 2, 2),
        ),
    )

    plan = build_continuous_futures_acquisition_plan(
        windows,
        source="angelone",
        timeframe="1m",
        interval_ns=60_000_000_000,
        calendar=calendar,
        max_request_ns=86_400_000_000_000,
    )

    sessions = calendar.sessions_between(date(2026, 1, 28), date(2026, 2, 2))
    assert [request.instrument for request in plan.requests] == [
        "NFO:101",
        "NFO:101",
        "NFO:202",
        "NFO:202",
        "NFO:202",
    ]
    assert [(request.start_ns, request.end_ns) for request in plan.requests] == [
        (sessions[0].start_ns, sessions[0].end_ns),
        (sessions[1].start_ns, sessions[1].end_ns),
        (sessions[2].start_ns, sessions[2].end_ns),
        (sessions[3].start_ns, sessions[3].end_ns),
        (sessions[4].start_ns, sessions[4].end_ns),
    ]


def test_rollover_transition_rejects_gap_between_old_and_new_contract_windows():
    windows = (
        FNORolloverWindow("AAA", "STOCK_FUTURE", "101", date(2026, 1, 28), date(2026, 1, 29)),
        FNORolloverWindow("AAA", "STOCK_FUTURE", "202", date(2026, 2, 2), date(2026, 2, 3)),
    )

    with pytest.raises(ValueError, match="gap or overlap"):
        build_continuous_futures_acquisition_plan(
            windows,
            source="angelone",
            timeframe="1m",
            interval_ns=60_000_000_000,
            calendar=TradingCalendar(),
            max_request_ns=86_400_000_000_000,
        )


def test_rollover_transition_rejects_overlap_between_old_and_new_contract_windows():
    windows = (
        FNORolloverWindow("AAA", "STOCK_FUTURE", "101", date(2026, 1, 28), date(2026, 1, 30)),
        FNORolloverWindow("AAA", "STOCK_FUTURE", "202", date(2026, 1, 30), date(2026, 2, 2)),
    )

    with pytest.raises(ValueError, match="overlap"):
        build_continuous_futures_acquisition_plan(
            windows,
            source="angelone",
            timeframe="1m",
            interval_ns=60_000_000_000,
            calendar=TradingCalendar(),
            max_request_ns=86_400_000_000_000,
        )
