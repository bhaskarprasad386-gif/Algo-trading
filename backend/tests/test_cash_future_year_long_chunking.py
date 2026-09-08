from datetime import datetime, timezone
from math import ceil

from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.cash_future_historical_download import build_chunked_plan


NANOSECOND = 1_000_000_000
DAY_NS = 24 * 60 * 60 * NANOSECOND
WEEK_NS = 7 * DAY_NS


def test_year_range_is_split_into_exact_bounded_weekly_requests():
    start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * NANOSECOND)
    end = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * NANOSECOND)
    request = HistoricalFetchRequest("fake", "NIFTY", "1m", start, end)

    plan = build_chunked_plan(request, WEEK_NS)
    requests = plan.requests

    expected_count = ceil((end - start + 1) / WEEK_NS)
    assert len(requests) == expected_count
    assert requests[0].start_ns == start
    assert requests[-1].end_ns == end

    for previous, current in zip(requests, requests[1:]):
        assert current.start_ns == previous.end_ns + 1

    for chunk in requests:
        assert chunk.start_ns <= chunk.end_ns
        assert chunk.end_ns - chunk.start_ns + 1 <= WEEK_NS

    covered = sum(chunk.end_ns - chunk.start_ns + 1 for chunk in requests)
    assert covered == end - start + 1


def test_leap_year_range_has_no_gap_or_overlap():
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * NANOSECOND)
    end = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * NANOSECOND)
    request = HistoricalFetchRequest("fake", "NIFTY", "1m", start, end)

    requests = build_chunked_plan(request, WEEK_NS).requests

    assert requests[0].start_ns == start
    assert requests[-1].end_ns == end
    assert all(
        current.start_ns == previous.end_ns + 1
        for previous, current in zip(requests, requests[1:])
    )
    assert sum(chunk.end_ns - chunk.start_ns + 1 for chunk in requests) == end - start + 1
