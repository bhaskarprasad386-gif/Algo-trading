from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.backtesting.cash_future_universe_acquisition import (
    CashFutureUniverseAcquisitionResult,
    acquire_cash_future_universe,
)
from app.scanner.cash_future_universe import CashFutureFnoUniverse, CashFutureUniverseItem
from app.scanner.session_gap_planner import SessionWindow


def test_requires_durable_run_id_pair():
    with pytest.raises(ValueError, match="job_store and run_id"):
        acquire_cash_future_universe(
            service=object(),
            universe=CashFutureFnoUniverse((), ()),
            master_rows=(),
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            spot_sessions_by_underlying={},
            job_store=object(),
        )


def test_rejects_reversed_date_range():
    with pytest.raises(ValueError, match="end must not precede start"):
        acquire_cash_future_universe(
            service=object(),
            universe=CashFutureFnoUniverse((), ()),
            master_rows=(),
            start=datetime(2026, 1, 2, tzinfo=timezone.utc),
            end=datetime(2026, 1, 1, tzinfo=timezone.utc),
            spot_sessions_by_underlying={},
        )


def test_pending_chunks_excludes_completed_and_skipped_chunks():
    result = CashFutureUniverseAcquisitionResult(
        (
            SimpleNamespace(
                plan=SimpleNamespace(requests=(1, 2, 3, 4)),
                execution=SimpleNamespace(
                    completed_chunks=2,
                    skipped_chunks=1,
                    processed_chunks=3,
                ),
                coverage=SimpleNamespace(status="INCOMPLETE"),
            ),
        )
    )

    assert result.completed_chunks == 2
    assert result.pending_chunks == 1
    assert len(result.incomplete) == 1


def test_orchestrates_each_stock_with_master_cash_and_exact_future_sessions():
    start = datetime(2026, 9, 10, 9, 15, tzinfo=timezone.utc)
    end = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)
    session = SessionWindow(
        start_ns=int(start.timestamp() * 1_000_000_000),
        end_ns=int(end.timestamp() * 1_000_000_000),
    )
    universe = CashFutureFnoUniverse(
        stocks=(
            CashFutureUniverseItem(
                underlying="ABC",
                contract_month="2026-10",
                future_token="999",
                future_symbol="ABC26OCT",
                expiry=date(2026, 10, 29),
                lot_size=125,
            ),
        ),
        indices=(),
    )
    master_rows = (
        {
            "exch_seg": "NSE",
            "symbol": "ABC-EQ",
            "name": "ABC",
            "token": "123",
            "instrumenttype": "",
        },
    )

    calls = []

    class FakeService:
        @staticmethod
        def _session_days(sessions):
            return (date(2026, 9, 10),)

        def acquire(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                plan=SimpleNamespace(requests=(1, 2)),
                execution=SimpleNamespace(completed_chunks=2, processed_chunks=2),
                coverage=SimpleNamespace(status="READY"),
            )

    result = acquire_cash_future_universe(
        service=FakeService(),
        universe=universe,
        master_rows=master_rows,
        start=start,
        end=end,
        spot_sessions_by_underlying={"ABC": (session,)},
        future_sessions_by_instrument={"NFO:999:ABC26OCT": (session,)},
    )

    assert len(calls) == 1
    assert calls[0]["underlying"] == "ABC"
    assert calls[0]["spot_instrument"] == "NSE:123:ABC-EQ"
    assert calls[0]["queue"].spot.instrument == "NSE:123:ABC-EQ"
    assert tuple(request.instrument for request in calls[0]["queue"].futures) == (
        "NFO:999:ABC26OCT",
    )
    assert calls[0]["future_sessions"]["NFO:999:ABC26OCT"] == (session,)
    assert result.completed_chunks == 2
    assert result.pending_chunks == 0
    assert result.incomplete == ()
