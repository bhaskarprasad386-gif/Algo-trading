"""Run all-stock Cash-Future acquisition and materialize its durable backtest history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterable, Mapping

from sqlalchemy.orm import Session

from .cash_future_historical_acquisition import CashFutureAcquisitionProgress, CashFutureHistoricalAcquisitionService
from .cash_future_universe import CashFutureFnoUniverse
from .cash_future_universe_acquisition import CashFutureUniverseAcquisitionResult, acquire_cash_future_universe
from .cash_future_universe_download_plan import CashFutureUniverseDownloadJob, CashFutureUniverseDownloadPlan
from .cash_future_universe_materializer import materialize_cash_future_universe_history
from .historical_catalog import HistoricalCatalog
from .historical_job_store import HistoricalJobStore
from .provider_retry import ProviderRetryPolicy
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class CashFutureUniversePipelineResult:
    acquisition: CashFutureUniverseAcquisitionResult
    materialized_rows: int


def acquire_and_materialize_cash_future_universe(
    *,
    service: CashFutureHistoricalAcquisitionService,
    universe: CashFutureFnoUniverse,
    master_rows: Iterable[Mapping[str, object]],
    start: datetime,
    end: datetime,
    spot_sessions_by_underlying: Mapping[str, tuple[SessionWindow, ...]],
    db: Session,
    catalog: HistoricalCatalog,
    future_sessions_by_instrument: Mapping[str, tuple[SessionWindow, ...]] | None = None,
    timeframe: str = "1m",
    source: str = "angelone",
    mode: str = "BOTH",
    retry_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
    retry_policy: ProviderRetryPolicy | None = None,
    max_repair_passes: int = 3,
    job_store: HistoricalJobStore | None = None,
    run_id: str | None = None,
    job_id_prefix: str = "cash-future",
    on_progress: Callable[[str, CashFutureAcquisitionProgress], None] | None = None,
    coverage_store=None,
    margin_required: float = 0.0,
    batch_size: int = 1000,
) -> CashFutureUniversePipelineResult:
    """Acquire the planned universe durably, then materialize downloaded bars.

    Materialization uses each acquisition result's exact queue, so repaired or
    rollover-specific requests remain the single source of instrument identity.
    Only persisted catalog rows are read; raw historical data is never accumulated
    in the pipeline result.
    """
    acquisition = acquire_cash_future_universe(
        service=service,
        universe=universe,
        master_rows=master_rows,
        start=start,
        end=end,
        spot_sessions_by_underlying=spot_sessions_by_underlying,
        future_sessions_by_instrument=future_sessions_by_instrument,
        timeframe=timeframe,
        source=source,
        mode=mode,
        retry_attempts=retry_attempts,
        retry_delay_seconds=retry_delay_seconds,
        retry_policy=retry_policy,
        max_repair_passes=max_repair_passes,
        job_store=job_store,
        run_id=run_id,
        job_id_prefix=job_id_prefix,
        on_progress=on_progress,
        coverage_store=coverage_store,
    )

    metadata = {
        (item.underlying.upper(), item.future_token): item
        for item in universe.stocks
    }
    materialized_rows = 0
    for result in acquisition.results:
        jobs = []
        for request in result.queue.futures:
            parts = request.instrument.split(":", 2)
            if len(parts) != 3:
                raise ValueError(f"invalid future instrument: {request.instrument}")
            item = metadata.get((result.queue.spot.instrument.split(":", 2)[2].split("-", 1)[0].upper(), parts[1]))
            if item is None:
                for candidate in universe.stocks:
                    if candidate.future_token == parts[1] and candidate.future_symbol == parts[2]:
                        item = candidate
                        break
            if item is None:
                raise ValueError(f"acquisition queue future has no universe metadata: {request.instrument}")
        jobs.append(CashFutureUniverseDownloadJob(
            result.queue.spot.instrument.split(":", 2)[2].split("-", 1)[0],
            result.queue.spot,
            result.queue.futures,
        ))
        plan = CashFutureUniverseDownloadPlan(tuple(jobs), __import__("app.backtesting.historical_sync", fromlist=["HistoricalSyncPlan"]).HistoricalSyncPlan(result.queue.all_requests))
        materialized_rows += materialize_cash_future_universe_history(
            db,
            catalog,
            download_plan=plan,
            universe=universe,
            source=source,
            timeframe=timeframe,
            margin_required=margin_required,
            batch_size=batch_size,
        )

    return CashFutureUniversePipelineResult(acquisition, materialized_rows)


__all__ = ["CashFutureUniversePipelineResult", "acquire_and_materialize_cash_future_universe"]
