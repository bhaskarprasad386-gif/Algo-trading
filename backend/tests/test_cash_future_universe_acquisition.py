from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.backtesting.cash_future_universe_acquisition import (
    CashFutureUniverseAcquisitionResult,
    acquire_cash_future_universe,
)
from app.scanner.cash_future_universe import CashFutureFnoUniverse
from app.scanner.session_gap_planner import SessionWindow


def test_requires_durable_run_id_pair():
    with pytest.raises(ValueError, match="job_store and run_id"):
        acquire_cash_future_universe(
            service=object(),
            universe=CashFutureFnoUniverse(()),
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
            universe=CashFutureFnoUniverse(()),
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
