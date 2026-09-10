from datetime import date, datetime

from backend.app.backtesting.cash_future_universe import (
    CashFutureFnoUniverse,
    CashFutureUniverseItem,
    IndexFutureUniverseItem,
)
from backend.app.backtesting.cash_future_universe_download_plan import (
    build_cash_future_universe_download_plan,
)


START = datetime(2026, 9, 10, 10, 0, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))
END = datetime(2026, 11, 30, 14, 0, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))


def _universe() -> CashFutureFnoUniverse:
    return CashFutureFnoUniverse(
        stocks=(
            CashFutureUniverseItem("XYZ", "2026-10", "201", "XYZ26OCT", date(2026, 10, 29), 100),
            CashFutureUniverseItem("ABC", "2026-11", "102", "ABC26NOV", date(2026, 11, 26), 125),
            CashFutureUniverseItem("ABC", "2026-10", "101", "ABC26OCT", date(2026, 10, 29), 125),
        ),
        indices=(
            IndexFutureUniverseItem("NFO", "NIFTY", "2026-10", "999", "NIFTY26OCT", date(2026, 10, 29), 75),
        ),
    )


def _master_rows():
    return (
        {"exch_seg": "NSE", "name": "ABC", "symbol": "ABC-EQ", "token": "11", "instrumenttype": "EQ"},
        {"exch_seg": "NSE", "name": "XYZ", "symbol": "XYZ-EQ", "token": "22", "instrumenttype": "EQ"},
        {"exch_seg": "NFO", "name": "NIFTY", "symbol": "NIFTY26OCT", "token": "999", "instrumenttype": "FUTIDX"},
    )


def test_all_stock_underlyings_and_contract_months_are_planned_exactly():
    result = build_cash_future_universe_download_plan(
        universe=_universe(),
        master_rows=_master_rows(),
        start=START,
        end=END,
        session_days=(date(2026, 9, 10), date(2026, 10, 29), date(2026, 11, 26)),
    )

    assert [job.underlying for job in result.jobs] == ["ABC", "XYZ"]
    assert result.jobs[0].spot.instrument == "NSE:11:ABC-EQ"
    assert result.jobs[1].spot.instrument == "NSE:22:XYZ-EQ"
    assert [request.instrument for request in result.jobs[0].futures] == [
        "NFO:101:ABC26OCT",
        "NFO:102:ABC26NOV",
    ]
    assert [request.instrument for request in result.jobs[1].futures] == ["NFO:201:XYZ26OCT"]
    assert all("NIFTY" not in request.instrument for request in result.requests)


def test_contract_requests_are_session_bounded_and_stop_at_expiry():
    result = build_cash_future_universe_download_plan(
        universe=_universe(),
        master_rows=_master_rows(),
        start=START,
        end=END,
        session_days=(date(2026, 9, 10), date(2026, 10, 29), date(2026, 11, 26)),
    )

    october = result.jobs[0].futures[0]
    assert october.start_ns < october.end_ns
    assert datetime.fromtimestamp(october.end_ns / 1_000_000_000, tz=__import__("datetime").timezone.utc).date() == date(2026, 10, 29)


def test_plan_order_is_deterministic():
    kwargs = dict(
        universe=_universe(),
        master_rows=_master_rows(),
        start=START,
        end=END,
        session_days=(date(2026, 9, 10), date(2026, 10, 29), date(2026, 11, 26)),
    )
    first = build_cash_future_universe_download_plan(**kwargs)
    second = build_cash_future_universe_download_plan(**kwargs)
    assert first.requests == second.requests
