"""Catalog-aware planning that downloads only missing fixed-cadence F&O history."""

from __future__ import annotations

from datetime import date

from .fno_acquisition import FNOAcquisitionJob, FNOAcquisitionPlan
from .fno_universe import FNOUniverse
from .historical_catalog import HistoricalCatalog
from .trading_calendar import TradingCalendar


def build_missing_fno_acquisition_plan(
    universe: FNOUniverse,
    *,
    catalog: HistoricalCatalog,
    source: str,
    as_of: date,
    timeframe: str,
    start_ns: int,
    end_ns: int,
    max_request_ns: int,
    interval_ns: int,
    calendar: TradingCalendar,
    start_date: date,
    end_date: date,
) -> FNOAcquisitionPlan:
    """Build jobs from durable coverage, preserving only genuinely missing bars.

    This planner is intentionally for fixed-cadence bars. Existing timestamps are
    excluded, closed days/session boundaries are never treated as missing, and an
    instrument with no stored coverage receives the complete requested range.
    """
    if not source.strip():
        raise ValueError("source is required")
    if start_ns < 0 or end_ns < start_ns:
        raise ValueError("invalid acquisition range")
    if interval_ns <= 0:
        raise ValueError("interval_ns must be positive")
    if max_request_ns <= 0:
        raise ValueError("max_request_ns must be positive")

    jobs: list[FNOAcquisitionJob] = []
    for contract in universe.stock_contracts + universe.index_contracts:
        timestamps = catalog.timestamps(
            source=source,
            instrument=contract.token,
            timeframe=timeframe,
            start_ns=start_ns,
            end_ns=end_ns,
        )
        if not timestamps:
            ranges = ((start_ns, end_ns),)
        else:
            ranges = tuple(
                (gap.start_ns, gap.end_ns)
                for gap in catalog.session_gaps(
                    source=source,
                    instrument=contract.token,
                    timeframe=timeframe,
                    interval_ns=interval_ns,
                    calendar=calendar,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
            # Coverage before the first observed bar and after the last observed
            # bar is also missing; only bounded session-aware gaps are delegated
            # to the catalog method above.
            ranges = _add_edge_ranges(
                ranges,
                timestamps=timestamps,
                start_ns=start_ns,
                end_ns=end_ns,
                interval_ns=interval_ns,
            )

        for range_start, range_end in ranges:
            cursor = range_start
            while cursor <= range_end:
                chunk_end = min(range_end, cursor + max_request_ns - 1)
                jobs.append(
                    FNOAcquisitionJob(
                        instrument=contract.token,
                        timeframe=timeframe,
                        start_ns=cursor,
                        end_ns=chunk_end,
                        kind=contract.instrument_type,
                    )
                )
                cursor = chunk_end + 1

    return FNOAcquisitionPlan(as_of=as_of, jobs=tuple(jobs))


def _add_edge_ranges(
    ranges: tuple[tuple[int, int], ...],
    *,
    timestamps: tuple[int, ...],
    start_ns: int,
    end_ns: int,
    interval_ns: int,
) -> tuple[tuple[int, int], ...]:
    """Add only cadence-aligned leading/trailing ranges around known coverage."""
    result = list(ranges)
    first = timestamps[0]
    last = timestamps[-1]
    if first > start_ns:
        result.append((start_ns, first - interval_ns))
    if last < end_ns:
        result.append((last + interval_ns, end_ns))
    return tuple((max(start_ns, a), min(end_ns, b)) for a, b in result if a <= b)


__all__ = ["build_missing_fno_acquisition_plan"]
