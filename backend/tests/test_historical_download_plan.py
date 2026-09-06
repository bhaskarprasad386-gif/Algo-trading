from datetime import datetime, timedelta, timezone

import pytest

from app.backtesting.historical_download_plan import build_one_year_plan, one_year_range


def test_one_year_range_is_calendar_lookback():
    end = datetime(2026, 9, 6, tzinfo=timezone.utc)
    start_ns, end_ns = one_year_range(end=end)
    assert end_ns > start_ns


def test_leap_day_lookback_is_valid():
    start_ns, end_ns = one_year_range(end=datetime(2024, 2, 29, tzinfo=timezone.utc))
    start = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
    end = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc)
    assert start == datetime(2023, 2, 28, tzinfo=timezone.utc)
    assert end == datetime(2024, 2, 29, tzinfo=timezone.utc)


def test_one_year_plan_is_chunked_and_non_overlapping():
    plan = build_one_year_plan(
        source="angelone", instrument="NSE:3045:SBIN", timeframe="1m",
        end=datetime(2026, 9, 6, tzinfo=timezone.utc), chunk=timedelta(days=7)
    )
    assert len(plan.requests) > 50
    for left, right in zip(plan.requests, plan.requests[1:]):
        assert right.start_ns == left.end_ns + 1


def test_one_year_plan_rejects_non_positive_chunk():
    with pytest.raises(ValueError):
        build_one_year_plan(
            source="angelone", instrument="NSE:3045:SBIN", timeframe="1m",
            end=datetime(2026, 9, 6, tzinfo=timezone.utc), chunk=timedelta(0)
        )
