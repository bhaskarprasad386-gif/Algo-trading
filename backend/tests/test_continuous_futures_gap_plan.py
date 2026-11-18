from datetime import date

from app.backtesting.continuous_futures_acquisition import build_continuous_futures_gap_plan
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.trading_calendar import TradingCalendar


def test_cash_future_gap_plan_uses_active_contract_sessions(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    calendar = TradingCalendar()
    window = FNORolloverWindow(
        underlying="ABC",
        instrument_type="FUT",
        contract_token="123",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
    )

    plan = build_continuous_futures_gap_plan(
        catalog,
        [window],
        source="test",
        timeframe="1m",
        interval_ns=60_000_000_000,
        calendar=calendar,
        max_request_ns=300_000_000_000,
    )

    assert all(request.instrument == "NFO:123" for request in plan.requests)
    assert all(request.source == "test" for request in plan.requests)
    assert all(request.timeframe == "1m" for request in plan.requests)
    assert plan.requests


def test_cash_future_gap_plan_returns_no_requests_when_catalog_is_complete(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "catalog.sqlite")
    calendar = TradingCalendar()
    window = FNORolloverWindow(
        underlying="ABC",
        instrument_type="FUT",
        contract_token="123",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
    )

    sessions = calendar.sessions_between(window.start_date, window.end_date)
    session = sessions[0]
    timestamps = range(session.start_ns, session.end_ns + 1, 60_000_000_000)
    catalog.ingest(
        HistoricalRecord(
            source="test",
            instrument="NFO:123",
            timeframe="1m",
            timestamp_ns=timestamp,
            payload={"close": 1.0},
        )
        for timestamp in timestamps
    )

    plan = build_continuous_futures_gap_plan(
        catalog,
        [window],
        source="test",
        timeframe="1m",
        interval_ns=60_000_000_000,
        calendar=calendar,
        max_request_ns=300_000_000_000,
    )

    assert plan.requests == ()
