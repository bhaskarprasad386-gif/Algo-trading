from datetime import date, time

from app.backtesting.contract_master import ContractRecord
from app.backtesting.fno_acquisition import build_fno_coverage_plan
from app.backtesting.fno_universe import build_fno_universe
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.trading_calendar import TradingCalendar


def make_universe():
    return build_fno_universe(
        [ContractRecord("NFO", "TCS", "101", date(2026, 9, 24), "STOCK_FUTURE", "TCS", 175)],
        snapshot_date=date(2026, 9, 1),
    )


def make_plan(catalog, calendar, session, interval, *, max_request_ns=None):
    return build_fno_coverage_plan(
        make_universe(),
        as_of=date(2026, 9, 7),
        timeframe="1m",
        start_ns=session.start_ns,
        end_ns=session.end_ns,
        max_request_ns=max_request_ns or interval,
        catalog=catalog,
        source="provider",
        interval_ns=interval,
        calendar=calendar,
        start_date=date(2026, 9, 7),
        end_date=date(2026, 9, 7),
    )


def test_coverage_plan_repairs_leading_interior_and_trailing_session_gaps():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 20))
    session = calendar.sessions_between(date(2026, 9, 7), date(2026, 9, 7))[0]
    interval = 60 * 1_000_000_000
    catalog.ingest(
        HistoricalRecord("provider", "101", "1m", session.start_ns + 2 * interval, {"close": 100})
    )

    plan = make_plan(catalog, calendar, session, interval)

    assert [(job.start_ns, job.end_ns) for job in plan.jobs] == [
        (session.start_ns, session.start_ns + interval),
        (session.start_ns + 3 * interval, session.start_ns + 4 * interval),
    ]


def test_coverage_plan_does_not_cross_session_boundary():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 20))
    sessions = calendar.sessions_between(date(2026, 9, 7), date(2026, 9, 8))
    interval = 60 * 1_000_000_000
    catalog.ingest(
        HistoricalRecord("provider", "101", "1m", sessions[0].start_ns + 4 * interval, {"close": 100}),
        HistoricalRecord("provider", "101", "1m", sessions[1].start_ns, {"close": 100}),
    )

    plan = build_fno_coverage_plan(
        make_universe(),
        as_of=date(2026, 9, 7),
        timeframe="1m",
        start_ns=sessions[0].start_ns,
        end_ns=sessions[1].end_ns,
        max_request_ns=interval,
        catalog=catalog,
        source="provider",
        interval_ns=interval,
        calendar=calendar,
        start_date=date(2026, 9, 7),
        end_date=date(2026, 9, 8),
    )

    assert all(
        not (sessions[0].end_ns < job.start_ns < sessions[1].start_ns)
        for job in plan.jobs
    )


def test_coverage_plan_uses_bounded_chunks_for_missing_session_edges():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 20))
    session = calendar.sessions_between(date(2026, 9, 7), date(2026, 9, 7))[0]
    interval = 1

    plan = make_plan(catalog, calendar, session, interval, max_request_ns=2)

    assert [(job.start_ns, job.end_ns) for job in plan.jobs] == [
        (session.start_ns, session.start_ns + 1),
        (session.start_ns + 2, session.start_ns + 3),
        (session.start_ns + 4, session.start_ns + 4),
    ]


def test_coverage_plan_skips_sessions_after_contract_expiry():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 20))
    sessions = calendar.sessions_between(date(2026, 9, 24), date(2026, 9, 25))
    interval = 60 * 1_000_000_000

    plan = build_fno_coverage_plan(
        make_universe(),
        as_of=date(2026, 9, 25),
        timeframe="1m",
        start_ns=sessions[0].start_ns,
        end_ns=sessions[1].end_ns,
        max_request_ns=interval,
        catalog=catalog,
        source="provider",
        interval_ns=interval,
        calendar=calendar,
        start_date=date(2026, 9, 24),
        end_date=date(2026, 9, 25),
    )

    assert plan.job_count > 0
    assert all(job.start_ns < sessions[1].start_ns for job in plan.jobs)
