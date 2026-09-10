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


def build_fno_acquisition_plan(
    universe: FNOUniverse,
    *,
    as_of: date,
    timeframe: str,
    start_ns: int,
    end_ns: int,
    max_request_ns: int,
) -> FNOAcquisitionPlan:
    """Create bounded jobs for every provider-backed stock/index future contract.

    Range splitting is transport-only; use ``build_fno_coverage_plan`` when a
    durable catalog is available and only missing coverage should be requested.
    """
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
    """Plan only missing fixed-cadence F&O coverage from the durable catalog.

    Existing timestamps are never downloaded again. Gaps are evaluated inside
    trading sessions, so overnight/weekend/closed-day boundaries are not treated
    as missing bars. A contract with no stored timestamps in the requested range
    receives the normal bounded full-range acquisition jobs.
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
    for contract in universe.stock_contracts + universe.index_contracts:
        observed = catalog.timestamps(
            source=source,
            instrument=contract.token,
            timeframe=timeframe,
            start_ns=start_ns,
            end_ns=end_ns,
        )
        if not observed:
            jobs.extend(_split_jobs(
                instrument=contract.token,
                timeframe=timeframe,
                kind=contract.instrument_type,
                start_ns=start_ns,
                end_ns=end_ns,
                max_request_ns=max_request_ns,
            ))
            continue

        gaps = catalog.session_gaps(
            source=source,
            instrument=contract.token,
            timeframe=timeframe,
            interval_ns=interval_ns,
            calendar=calendar,
            start_date=start_date,
            end_date=end_date,
        )
        for gap in gaps:
            gap_start = max(start_ns, gap.start_ns)
            gap_end = min(end_ns, gap.end_ns)
            if gap_start <= gap_end:
                jobs.extend(_split_jobs(
                    instrument=contract.token,
                    timeframe=timeframe,
                    kind=contract.instrument_type,
                    start_ns=gap_start,
                    end_ns=gap_end,
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
