"""Production-safe entry point for bounded Angel One Cash-Future history runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping

from app.algo.auth import AngelOneAuth

from .angelone_cash_future_acquisition import build_angelone_cash_future_acquisition_service
from .cash_future_universe_acquisition import CashFutureUniverseAcquisitionResult, acquire_cash_future_universe
from .cash_future_universe import CashFutureFnoUniverse
from .historical_ingest import HistoricalIngestionService
from .historical_job_store import HistoricalJobStore
from .provider_retry import ProviderRetryPolicy
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class AngelOneCashFutureRunConfig:
    interval_ns: int
    max_request_ns: int
    timeframe: str = "1m"
    mode: str = "BOTH"
    chunk_days: int = 30
    retry_attempts: int = 3
    retry_delay_seconds: float = 1.0
    max_repair_passes: int = 3


def run_angelone_cash_future_history(
    *,
    ingestion: HistoricalIngestionService,
    contract_master,
    universe: CashFutureFnoUniverse,
    master_rows: Iterable[Mapping[str, object]],
    start: datetime,
    end: datetime,
    spot_sessions_by_underlying: Mapping[str, tuple[SessionWindow, ...]],
    future_sessions_by_instrument: Mapping[str, tuple[SessionWindow, ...]] | None = None,
    job_store: HistoricalJobStore | None = None,
    run_id: str | None = None,
    config: AngelOneCashFutureRunConfig,
    auth: AngelOneAuth | None = None,
    limiter=None,
    retry_policy: ProviderRetryPolicy | None = None,
    on_progress: Callable[[str, object], None] | None = None,
    coverage_store=None,
) -> CashFutureUniverseAcquisitionResult:
    """Run a bounded real Angel One history acquisition with durable resume support."""
    auth = auth or AngelOneAuth()
    auth.get_client()

    service = build_angelone_cash_future_acquisition_service(
        ingestion,
        contract_master,
        interval_ns=config.interval_ns,
        max_request_ns=config.max_request_ns,
        auth=auth,
        limiter=limiter,
        chunk_days=config.chunk_days,
    )

    return acquire_cash_future_universe(
        service=service,
        universe=universe,
        master_rows=master_rows,
        start=start,
        end=end,
        spot_sessions_by_underlying=spot_sessions_by_underlying,
        future_sessions_by_instrument=future_sessions_by_instrument,
        timeframe=config.timeframe,
        mode=config.mode,
        retry_attempts=config.retry_attempts,
        retry_delay_seconds=config.retry_delay_seconds,
        retry_policy=retry_policy,
        max_repair_passes=config.max_repair_passes,
        job_store=job_store,
        run_id=run_id,
        on_progress=on_progress,
        coverage_store=coverage_store,
    )


__all__ = ["AngelOneCashFutureRunConfig", "run_angelone_cash_future_history"]
