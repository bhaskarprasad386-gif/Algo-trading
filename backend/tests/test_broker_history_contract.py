from datetime import datetime, timedelta, timezone

import pytest

from app.brokers.history import BrokerHistoryWindow
from app.execution.broker_reconciliation import BrokerSnapshot


@pytest.fixture
def window_bounds():
    start = datetime(2026, 11, 16, 3, 30, tzinfo=timezone.utc)
    return start, start + timedelta(hours=6)


def _snapshot(*, complete=True, account_id="acct-1", broker="angel_one"):
    return BrokerSnapshot(
        account_id=account_id,
        broker=broker,
        orders=(),
        trades=(),
        complete=complete,
    )


def test_complete_history_window_requires_complete_snapshot_and_finished_pages(window_bounds):
    start, end = window_bounds
    result = BrokerHistoryWindow(
        snapshot=_snapshot(), window_start=start, window_end=end,
        pages_fetched=2, has_more=False, provider_window="CURRENT_TRADING_DAY",
    )
    assert result.complete is True
    assert result.pages_fetched == 2
    assert result.provider_window == "CURRENT_TRADING_DAY"


def test_incomplete_provider_snapshot_never_becomes_complete(window_bounds):
    start, end = window_bounds
    result = BrokerHistoryWindow(
        snapshot=_snapshot(complete=False), window_start=start, window_end=end,
        pages_fetched=1, has_more=False,
    )
    assert result.complete is False


def test_pagination_cursor_means_window_is_not_complete(window_bounds):
    start, end = window_bounds
    result = BrokerHistoryWindow(
        snapshot=_snapshot(complete=False), window_start=start, window_end=end,
        pages_fetched=1, has_more=True, next_cursor="next-page",
    )
    assert result.complete is False
    assert result.next_cursor == "next-page"


@pytest.mark.parametrize("field,value", [
    ("window_start", datetime(2026, 11, 16, 3, 30)),
    ("window_end", datetime(2026, 11, 16, 9, 30)),
])
def test_history_window_rejects_naive_timestamps(window_bounds, field, value):
    start, end = window_bounds
    args = dict(
        snapshot=_snapshot(), window_start=start, window_end=end,
        pages_fetched=1, has_more=False,
    )
    args[field] = value
    with pytest.raises(ValueError, match="timezone-aware"):
        BrokerHistoryWindow(**args)


def test_history_window_rejects_reversed_or_empty_range(window_bounds):
    start, end = window_bounds
    with pytest.raises(ValueError, match="later"):
        BrokerHistoryWindow(
            snapshot=_snapshot(), window_start=end, window_end=start,
            pages_fetched=1, has_more=False,
        )


def test_history_window_rejects_missing_pagination_cursor(window_bounds):
    start, end = window_bounds
    with pytest.raises(ValueError, match="continuation cursor"):
        BrokerHistoryWindow(
            snapshot=_snapshot(complete=False), window_start=start, window_end=end,
            pages_fetched=1, has_more=True,
        )


def test_history_window_rejects_complete_snapshot_with_more_pages(window_bounds):
    start, end = window_bounds
    with pytest.raises(ValueError, match="cannot be complete"):
        BrokerHistoryWindow(
            snapshot=_snapshot(complete=True), window_start=start, window_end=end,
            pages_fetched=1, has_more=True, next_cursor="next-page",
        )


def test_history_window_rejects_cursor_when_pagination_finished(window_bounds):
    start, end = window_bounds
    with pytest.raises(ValueError, match="must be absent"):
        BrokerHistoryWindow(
            snapshot=_snapshot(), window_start=start, window_end=end,
            pages_fetched=1, has_more=False, next_cursor="stale-cursor",
        )


def test_history_window_requires_at_least_one_page(window_bounds):
    start, end = window_bounds
    with pytest.raises(ValueError, match="at least one"):
        BrokerHistoryWindow(
            snapshot=_snapshot(), window_start=start, window_end=end,
            pages_fetched=0, has_more=False,
        )
