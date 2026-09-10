"""Execute the all-stock Cash-Future acquisition plan through the durable service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping

from .cash_future_historical_acquisition import (
    CashFutureAcquisitionProgress,
    CashFutureAcquisitionResult,
    CashFutureHistoricalAcquisitionService,
)
from .cash_future_universe import CashFutureFnoUniverse
from .cash_future_universe_download_plan import build_cash_future_universe_download_plan
from .contract_master import ContractMasterCatalog
from .historical_job_store import HistoricalJobStore
from .historical_ingest import HistoricalSource
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class CashFutureUniverseAcquisitionResult:
    """Per-underlying durable outcomes; raw historical rows are never retained."""

    results: tuple[CashFutureAcquisitionResult, ...]

    @property
    def completed_chunks(self) -> int:
        return sum(result.execution.completed_chunks for result in self.results)

    @property
    def pending_chunks(self) -> int:
        return sum(len(result.plan.requests) for result in self.results)

    @property
    def incomplete(self) -> tuple[CashFutureAcquisitionResult, ...]:
        return tuple(result for result in self.results if result.coverage.status != "READY")


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
    """Execute every stock job from the master-backed universe, durably and sequentially.

    Index futures are intentionally excluded: they have no NSE equity cash leg.
    Each underlying gets its own durable job identity, so one failed stock can be
    resumed without replaying unrelated underlyings.
    """
    if end < start:
        raise ValueError("end must not precede start")
    if max_repair_passes < 1:
        raise ValueError("max_repair_passes must be positive")
    if (job_store is None) != (run_id is None):
        raise ValueError("job_store and run_id must be supplied together")

    # Validate/build the complete deterministic plan before execution. This also
    # verifies that every stock underlying has an unambiguous NSE cash token.
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

        future_sessions: dict[str, tuple[SessionWindow, ...]] = {}
        if future_sessions_by_instrument:
            for request in job.futures:
                if request.instrument in future_sessions_by_instrument:
                    future_sessions[request.instrument] = future_sessions_by_instrument[request.instrument]

        kwargs = {
            "spot_instrument": job.spot.instrument,
            "exchange": "NFO",
            "underlying": job.underlying,
            "start": start,
            "end": end,
            "spot_sessions": sessions,
            "future_sessions": future_sessions,
            "timeframe": timeframe,
            "mode": mode,
            "source": source,
            "max_repair_passes": max_repair_passes,
            "coverage_store": coverage_store,
        }
        if job_store is not None:
            kwargs.update(
                job_store=job_store,
                job_id=f"{job_id_prefix}:{job.underlying}",
                run_id=run_id,
            )

        def progress(event: CashFutureAcquisitionProgress, underlying: str = job.underlying) -> None:
            if on_progress is not None:
                on_progress(underlying, event)

        kwargs["on_progress"] = progress
        results.append(service.acquire(**kwargs))

    return CashFutureUniverseAcquisitionResult(tuple(results))


__all__ = ["CashFutureUniverseAcquisitionResult", "acquire_cash_future_universe"]
