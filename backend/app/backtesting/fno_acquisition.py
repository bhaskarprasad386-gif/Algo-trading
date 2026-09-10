"""Resumable historical acquisition planning for the provider-backed F&O universe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from .fno_universe import FNOUniverse
from .historical_ingest import HistoricalFetchRequest


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

    No contract or timestamp is fabricated. Range splitting is purely a transport
    concern; coverage/gap repair decides which jobs are actually still required.
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
        cursor = start_ns
        while cursor <= end_ns:
            chunk_end = min(end_ns, cursor + max_request_ns - 1)
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


__all__ = ["FNOAcquisitionJob", "FNOAcquisitionPlan", "build_fno_acquisition_plan", "to_fetch_requests"]
