from datetime import date

from app.backtesting.continuous_futures_acquisition import build_continuous_futures_acquisition_plan
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.trading_calendar import TradingCalendar


def test_continuous_futures_plan_respects_trading_session_boundaries_and_skips_weekend():
    windows = (
        FNORolloverWindow(
            underlying="AAA",
            instrument_type="STOCK_FUTURE",
            contract_token="101",
            start_date=date(2026, 1, 9),
            end_date=date(2026, 1, 12),
        ),
    )
    plan = build_continuous_futures_acquisition_plan(
        windows,
        source="angelone",
        timeframe="1m",
        interval_ns=60_000_000_000,
        calendar=TradingCalendar(),
        max_request_ns=86_400_000_000_000,
    )

    assert len(plan.requests) == 2
    sessions = TradingCalendar().sessions_between(date(2026, 1, 9), date(2026, 1, 12))
    assert [(r.instrument, r.start_ns, r.end_ns) for r in plan.requests] == [
        ("NFO:101", sessions[0].start_ns, sessions[0].end_ns),
        ("NFO:101", sessions[1].start_ns, sessions[1].end_ns),
    ]
