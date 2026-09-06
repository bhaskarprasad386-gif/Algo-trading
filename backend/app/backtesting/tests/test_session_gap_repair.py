from __future__ import annotations

from backend.app.backtesting.historical_catalog import Gap
from backend.app.backtesting.session_gap_repair import RepairRange, SessionGapRepairPlanner


def test_planner_ignores_non_session_timestamps_and_splits_runs() -> None:
    expected = {200, 300, 500}
    planner = SessionGapRepairPlanner(expected.__contains__)
    gaps = (Gap("NIFTY", "1m", 200, 500),)

    assert planner.plan(gaps, interval_ns=100) == (
        RepairRange("NIFTY", "1m", 200, 300),
        RepairRange("NIFTY", "1m", 500, 500),
    )


def test_planner_returns_empty_for_overnight_only_gap() -> None:
    planner = SessionGapRepairPlanner({200, 300}.__contains__)
    gaps = (Gap("NIFTY", "1m", 400, 900),)

    assert planner.plan(gaps, interval_ns=100) == ()
