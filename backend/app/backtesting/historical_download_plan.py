"""Provider-agnostic historical download planning and progress accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .historical_sync import HistoricalSyncPlan, build_chunked_plan


@dataclass(frozen=True)
class DownloadProgress:
    requested_chunks: int
    completed_chunks: int
    fetched_records: int
    inserted_records: int

    @property
    def complete(self) -> bool:
        return self.requested_chunks == self.completed_chunks


def utc_ns(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


def one_year_range(*, end: datetime) -> tuple[int, int]:
    """Return a calendar-year lookback ending at ``end`` without invalid dates."""
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    try:
        start = end.replace(year=end.year - 1)
    except ValueError:
        start = end.replace(year=end.year - 1, day=28)
    return utc_ns(start), utc_ns(end)


def build_one_year_plan(
    *,
    source: str,
    instrument: str,
    timeframe: str,
    end: datetime,
    chunk: timedelta | None = None,
    chunk_days: int | None = None,
) -> HistoricalSyncPlan:
    """Build a one-year plan.

    ``chunk`` is the canonical API; ``chunk_days`` remains supported for older
    callers and tests so the planner stays backwards compatible.
    """
    if chunk is not None and chunk_days is not None:
        raise ValueError("provide either chunk or chunk_days, not both")
    if chunk is None:
        days = 7 if chunk_days is None else chunk_days
        if days <= 0:
            raise ValueError("chunk_days must be positive")
        chunk = timedelta(days=days)
    start_ns, end_ns = one_year_range(end=end)
    chunk_ns = int(chunk.total_seconds() * 1_000_000_000)
    if chunk_ns <= 0:
        raise ValueError("chunk must be positive")
    return build_chunked_plan(
        source=source,
        instrument=instrument,
        timeframe=timeframe,
        start_ns=start_ns,
        end_ns=end_ns,
        chunk_ns=chunk_ns,
    )
