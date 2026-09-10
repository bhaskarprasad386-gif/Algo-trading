from datetime import timedelta

from app.backtesting.historical_catalog import Gap
from app.backtesting.historical_download_plan import build_gap_plan


def test_build_gap_plan_creates_only_missing_range():
    plan = build_gap_plan(
        source="angelone",
        gaps=(Gap("SBIN", "ONE_MINUTE", 100, 199),),
    )
    assert len(plan.requests) == 1
    request = plan.requests[0]
    assert (request.instrument, request.timeframe) == ("SBIN", "ONE_MINUTE")
    assert (request.start_ns, request.end_ns) == (100, 199)
    assert request.source == "angelone"


def test_build_gap_plan_splits_large_gap_into_bounded_chunks():
    plan = build_gap_plan(
        source="angelone",
        gaps=(Gap("SBIN", "ONE_MINUTE", 0, 299),),
        chunk=timedelta(seconds=100),
    )
    assert [(r.start_ns, r.end_ns) for r in plan.requests] == [
        (0, 99_999_999_999),
        (100_000_000_000, 199_999_999_999),
        (200_000_000_000, 299),
    ]


def test_build_gap_plan_is_empty_when_no_gaps():
    assert build_gap_plan(source="angelone", gaps=()).requests == ()
