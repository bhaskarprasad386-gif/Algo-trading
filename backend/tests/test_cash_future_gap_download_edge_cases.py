import pytest

from app.backtesting.cash_future_gap_download import CashFutureGapDownloadPlanner


def test_gap_planner_rejects_chunk_smaller_than_interval():
    with pytest.raises(ValueError, match="max_request_ns must be >= interval_ns"):
        CashFutureGapDownloadPlanner(
            interval_ns=60 * 1_000_000_000,
            max_request_ns=59 * 1_000_000_000,
        )


def test_gap_planner_accepts_single_interval_chunk():
    planner = CashFutureGapDownloadPlanner(
        interval_ns=60 * 1_000_000_000,
        max_request_ns=60 * 1_000_000_000,
    )
    assert planner.interval_ns == planner.max_request_ns
