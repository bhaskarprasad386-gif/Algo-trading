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


def test_coverage_plan_only_repairs_same_session_gap():
    catalog = HistoricalCatalog()
    start = TradingCalendar(session_open=time(9, 15), session_close=time(9, 20))
    sessions = start.sessions_between(date(2026, 9, 7), date(2026, 9, 7))
    session = sessions[0]
    interval = 60 * 1_000_000_000
    timestamps = (session.start_ns, session.start_ns + 2 * interval)
    catalog.ingest(
        HistoricalRecord("provider", "101", "1m", ts, {"close": 100}) for ts in timestamps
    )

    plan = build_fno_coverage_plan(
        make_universe(),
        as_of=date(2026, 9, 7),
        timeframe="1m",
        start_ns=session.start_ns,
        end_ns=session.end_ns,
        max_request_ns=interval,
        catalog=catalog,
        source="provider",
        interval_ns=interval,
        calendar=start,
        start_date=date(2026, 9, 7),
        end_date=date(2026, 9, 7),
    )

    assert [(job.start_ns, job.end_ns) for job in plan.jobs] == [
        (session.start_ns + interval, session.start_ns + interval)
    ]


def test_coverage_plan_uses_full_range_when_contract_has_no_data():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(9, 20))
    session = calendar.sessions_between(date(2026, 9, 7), date(2026, 9, 7))[0]
    plan = build_fno_coverage_plan(
        make_universe(),
        as_of=date(2026, 9, 7),
        timeframe="1m",
        start_ns=session.start_ns,
        end_ns=session.start_ns + 3,
        max_request_ns=2,
        catalog=catalog,
        source="provider",
        interval_ns=1,
        calendar=calendar,
        start_date=date(2026, 9, 7),
        end_date=date(2026, 9, 7),
    )
    assert [(job.start_ns, job.end_ns) for job in plan.jobs] == [
        (session.start_ns, session.start_ns + 1),
        (session.start_ns + 2, session.start_ns + 3),
    ]
