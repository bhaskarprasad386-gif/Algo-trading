"""Execute the all-stock Cash-Future acquisition plan through the durable service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping

from .cash_future_download_queue import CashFutureDownloadQueue
from .cash_future_historical_acquisition import (
    CashFutureAcquisitionProgress,
    CashFutureAcquisitionResult,
    CashFutureHistoricalAcquisitionService,
)
from .cash_future_universe import CashFutureFnoUniverse
from .cash_future_universe_download_plan import build_cash_future_universe_download_plan
from .historical_job_store import HistoricalJobStore
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class CashFutureUniverseAcquisitionResult:
    results: tuple[CashFutureAcquisitionResult, ...]

    @property
    def completed_chunks(self) -> int:
        return sum(r.execution.completed_chunks for r in self.results)

    @property
    def pending_chunks(self) -> int:
        """Return chunks that were neither completed nor skipped by execution."""
        return sum(
            max(0, len(r.plan.requests) - r.execution.processed_chunks)
            for r in self.results
        )

    @property
    def incomplete(self) -> tuple[CashFutureAcquisitionResult, ...]:
        return tuple(r for r in self.results if r.coverage.status != "READY")


def acquire_cash_future_universe(
    *,
    service: CashFutureHistoricalAcquisitionService,
    universe: CashFutureFnoUniverse,
    master_rows: Iterable[Mapping[str, object]],
    start: datetime,
    end: datetime,
    spot_sessions_by_underlying: Mapping[str, tuple[SessionWindow, ...]],
    future_sessions_by_instrument: Mapping[str, tuple[SessionWindow, ...]] | None = None,
    timeframe: str = "1m",
    source: str = "angelone",
    mode: str = "BOTH",
    max_repair_passes: int = 3,
    job_store: HistoricalJobStore | None = None,
    run_id: str | None = None,
    job_id_prefix: str = "cash-future",
    on_progress: Callable[[str, CashFutureAcquisitionProgress], None] | None = None,
    coverage_store=None,
) -> CashFutureUniverseAcquisitionResult:
    if end < start:
        raise ValueError("end must not precede start")
    if max_repair_passes < 1:
        raise ValueError("max_repair_passes must be positive")
    if (job_store is None) != (run_id is None):
        raise ValueError("job_store and run_id must be supplied together")

    plan = build_cash_future_universe_download_plan(
        universe=universe,
        master_rows=master_rows,
        start=start,
        end=end,
        timeframe=timeframe,
        source=source,
        session_days=(
            day
            for sessions in spot_sessions_by_underlying.values()
            for day in service._session_days(sessions)
        ),
    )

    results: list[CashFutureAcquisitionResult] = []
    for job in plan.jobs:
        sessions = spot_sessions_by_underlying.get(job.underlying)
        if not sessions:
            raise ValueError(f"missing spot sessions for {job.underlying}")

        future_sessions = {
            request.instrument: future_sessions_by_instrument[request.instrument]
            for request in job.futures
            if future_sessions_by_instrument
            and request.instrument in future_sessions_by_instrument
        }

        kwargs = dict(
            spot_instrument=job.spot.instrument,
            exchange="NFO",
            underlying=job.underlying,
            start=start,
            end=end,
            spot_sessions=sessions,
            future_sessions=future_sessions,
            timeframe=timeframe,
            mode=mode,
            source=source,
            max_repair_passes=max_repair_passes,
            coverage_store=coverage_store,
            on_progress=lambda event, underlying=job.underlying: (
                on_progress(underlying, event) if on_progress else None
            ),
            queue=CashFutureDownloadQueue(job.spot, job.futures),
        )
        if job_store is not None:
            kwargs.update(
                job_store=job_store,
                job_id=f"{job_id_prefix}:{job.underlying}",
                run_id=run_id,
            )
        results.append(service.acquire(**kwargs))

    return CashFutureUniverseAcquisitionResult(tuple(results))


__all__ = ["CashFutureUniverseAcquisitionResult", "acquire_cash_future_universe"]
