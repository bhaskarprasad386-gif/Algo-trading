"""Resumable historical acquisition planning for the provider-backed F&O universe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .fno_universe import FNOUniverse
from .historical_catalog import HistoricalCatalog
from .historical_ingest import HistoricalFetchRequest
from .trading_calendar import TradingCalendar


@dataclass(frozen=True)
class FNOAcquisitionJob:
    """One bounded, auditable acquisition request."""

    instrument: str
    timeframe: str
    start_ns: int
    end_ns: int
    kind: str


@dataclass(frozen=True)
class FNOAcquisitionPlan:
    as_of: date
    jobs: tuple[FNOAcquisitionJob, ...]

    @property
    def job_count(self) -> int:
        return len(self.jobs)


def _split_jobs(*, instrument: str, timeframe: str, kind: str, start_ns: int, end_ns: int, max_request_ns: int) -> list[FNOAcquisitionJob]:
    jobs: list[FNOAcquisitionJob] = []
    cursor = start_ns
    while cursor <= end_ns:
        chunk_end = min(end_ns, cursor + max_request_ns - 1)
        jobs.append(FNOAcquisitionJob(instrument, timeframe, cursor, chunk_end, kind))
        cursor = chunk_end + 1
    return jobs


def _missing_session_timestamps(
    *,
    observed: tuple[int, ...],
    session_start_ns: int,
    session_end_ns: int,
    start_ns: int,
    end_ns: int,
    interval_ns: int,
) -> tuple[tuple[int, int], ...]:
    """Return contiguous missing fixed-cadence timestamp ranges in one session."""
    lower = max(session_start_ns, start_ns)
    upper = min(session_end_ns, end_ns)
    if lower > upper or interval_ns <= 0:
        return ()

    first = session_start_ns + max(0, (lower - session_start_ns + interval_ns - 1) // interval_ns) * interval_ns
    last = session_start_ns + ((upper - session_start_ns) // interval_ns) * interval_ns
    if first > last or first >= session_end_ns:
        return ()
    last = min(last, session_end_ns - interval_ns)

    observed_set = set(observed)
    ranges: list[tuple[int, int]] = []
    cursor: int | None = None
    for timestamp in range(first, last + 1, interval_ns):
        if timestamp in observed_set:
            if cursor is not None:
                ranges.append((cursor, timestamp - interval_ns))
                cursor = None
            continue
        if cursor is None:
            cursor = timestamp
    if cursor is not None:
        ranges.append((cursor, last))
    return tuple(ranges)


def build_fno_acquisition_plan(
    universe: FNOUniverse,
    *,
    as_of: date,
    timeframe: str,
    start_ns: int,
    end_ns: int,
    max_request_ns: int,
) -> FNOAcquisitionPlan:
    """Create bounded jobs for every provider-backed stock/index future contract."""
    if not timeframe.strip():
        raise ValueError("timeframe is required")
    if start_ns < 0 or end_ns < start_ns:
        raise ValueError("invalid acquisition range")
    if max_request_ns <= 0:
        raise ValueError("max_request_ns must be positive")

    contracts = universe.stock_contracts + universe.index_contracts
    jobs: list[FNOAcquisitionJob] = []
    for contract in contracts:
        jobs.extend(_split_jobs(
            instrument=contract.token,
            timeframe=timeframe,
            kind=contract.instrument_type,
            start_ns=start_ns,
            end_ns=end_ns,
            max_request_ns=max_request_ns,
        ))
    return FNOAcquisitionPlan(as_of=as_of, jobs=tuple(jobs))


def build_fno_coverage_plan(
    universe: FNOUniverse,
    *,
    as_of: date,
    timeframe: str,
    start_ns: int,
    end_ns: int,
    max_request_ns: int,
    catalog: HistoricalCatalog,
    source: str,
    interval_ns: int,
    calendar: TradingCalendar,
    start_date: date,
    end_date: date,
) -> FNOAcquisitionPlan:
    """Plan only missing fixed-cadence F&O bars inside requested trading sessions.

    The expected grid is derived only from the declared fixed interval and the
    trading calendar. Existing timestamps are excluded, while leading, interior,
    and trailing missing bars are all repaired. Session boundaries are never
    crossed. Contracts are scheduled only through their expiry date. This planner
    is intentionally for fixed-cadence bars, not tick, depth, or event streams.
    """
    if not timeframe.strip() or not source.strip():
        raise ValueError("timeframe and source are required")
    if start_ns < 0 or end_ns < start_ns:
        raise ValueError("invalid acquisition range")
    if max_request_ns <= 0 or interval_ns <= 0:
        raise ValueError("max_request_ns and interval_ns must be positive")
    if end_date < start_date:
        raise ValueError("end_date cannot precede start_date")

    jobs: list[FNOAcquisitionJob] = []
    contracts = universe.stock_contracts + universe.index_contracts
    for contract in contracts:
        for session in calendar.sessions_between(start_date, end_date):
            if session.trading_date > contract.expiry:
                continue
            observed = catalog.timestamps(
                source=source,
                instrument=contract.token,
                timeframe=timeframe,
                start_ns=session.start_ns,
                end_ns=session.end_ns,
            )
            for missing_start, missing_end in _missing_session_timestamps(
                observed=observed,
                session_start_ns=session.start_ns,
                session_end_ns=session.end_ns,
                start_ns=start_ns,
                end_ns=end_ns,
                interval_ns=interval_ns,
            ):
                jobs.extend(_split_jobs(
                    instrument=contract.token,
                    timeframe=timeframe,
                    kind=contract.instrument_type,
                    start_ns=missing_start,
                    end_ns=missing_end,
                    max_request_ns=max_request_ns,
                ))

    return FNOAcquisitionPlan(as_of=as_of, jobs=tuple(jobs))


def to_fetch_requests(
    plan: FNOAcquisitionPlan, *, source: str
) -> tuple[HistoricalFetchRequest, ...]:
    """Convert planned jobs to the provider-agnostic ingestion request type."""
    return tuple(
        HistoricalFetchRequest(
            source=source,
            instrument=job.instrument,
            timeframe=job.timeframe,
            start_ns=job.start_ns,
            end_ns=job.end_ns,
        )
        for job in plan.jobs
    )


__all__ = [
    "FNOAcquisitionJob",
    "FNOAcquisitionPlan",
    "build_fno_acquisition_plan",
    "build_fno_coverage_plan",
    "to_fetch_requests",
]
