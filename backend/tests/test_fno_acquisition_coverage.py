from datetime import date, datetime, timezone

from app.backtesting.contract_master import ContractRecord
from app.backtesting.fno_acquisition_coverage import build_missing_fno_acquisition_plan
from app.backtesting.fno_universe import build_fno_universe
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.trading_calendar import TradingCalendar


def _ns(hour: int, minute: int) -> int:
    return int(datetime(2026, 9, 1, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_catalog_coverage_plans_only_missing_same_session_bars():
    universe = build_fno_universe(
        [ContractRecord("NFO", "TCS", "101", date(2026, 9, 24), "STOCK_FUTURE", "TCS", 175)],
        snapshot_date=date(2026, 9, 1),
    )
    catalog = HistoricalCatalog()
    try:
        catalog.ingest(
            [
                HistoricalRecord("provider", "101", "1m", _ns(9, 15), {"close": 1}),
                HistoricalRecord("provider", "101", "1m", _ns(9, 17), {"close": 3}),
            ]
        )
        plan = build_missing_fno_acquisition_plan(
            universe,
            catalog=catalog,
            source="provider",
            as_of=date(2026, 9, 1),
            timeframe="1m",
            start_ns=_ns(9, 15),
            end_ns=_ns(9, 17),
            max_request_ns=60_000_000_000,
            interval_ns=60_000_000_000,
            calendar=TradingCalendar(),
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        )
        assert [(job.start_ns, job.end_ns) for job in plan.jobs] == [(_ns(9, 16), _ns(9, 16))]
    finally:
        catalog.close()


def test_uncataloged_contract_gets_full_requested_range():
    universe = build_fno_universe(
        [ContractRecord("NFO", "NIFTY", "201", date(2026, 9, 24), "INDEX_FUTURE", "NIFTY", 65)],
        snapshot_date=date(2026, 9, 1),
    )
    catalog = HistoricalCatalog()
    try:
        plan = build_missing_fno_acquisition_plan(
            universe,
            catalog=catalog,
            source="provider",
            as_of=date(2026, 9, 1),
            timeframe="1m",
            start_ns=0,
            end_ns=119,
            max_request_ns=60,
            interval_ns=60,
            calendar=TradingCalendar(),
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        )
        assert [(job.start_ns, job.end_ns) for job in plan.jobs] == [(0, 59), (60, 119)]
    finally:
        catalog.close()
