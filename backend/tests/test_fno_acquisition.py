from datetime import date

from app.backtesting.contract_master import ContractRecord
from app.backtesting.fno_acquisition import build_fno_acquisition_plan, to_fetch_requests
from app.backtesting.fno_universe import build_fno_universe


def test_fno_acquisition_covers_stock_and_index_contracts_in_bounded_chunks():
    universe = build_fno_universe(
        [
            ContractRecord("NFO", "TCS", "101", date(2026, 9, 24), "STOCK_FUTURE", "TCS", 175),
            ContractRecord("NFO", "NIFTY", "201", date(2026, 9, 24), "INDEX_FUTURE", "NIFTY", 65),
        ],
        snapshot_date=date(2026, 9, 1),
    )

    plan = build_fno_acquisition_plan(
        universe,
        as_of=date(2026, 9, 1),
        timeframe="1m",
        start_ns=0,
        end_ns=9,
        max_request_ns=4,
    )

    assert plan.job_count == 6
    assert {job.instrument for job in plan.jobs} == {"101", "201"}
    assert all(job.end_ns - job.start_ns < 4 for job in plan.jobs)
    assert len(to_fetch_requests(plan, source="provider")) == 6


def test_empty_universe_produces_no_jobs():
    universe = build_fno_universe([], snapshot_date=date(2026, 9, 1))
    plan = build_fno_acquisition_plan(
        universe,
        as_of=date(2026, 9, 1),
        timeframe="1m",
        start_ns=0,
        end_ns=100,
        max_request_ns=10,
    )
    assert plan.jobs == ()
