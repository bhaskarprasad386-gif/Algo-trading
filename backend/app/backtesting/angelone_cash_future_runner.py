"""Production-safe entry point for bounded Angel One Cash-Future history runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping

from app.algo.auth import AngelOneAuth

from .angelone_cash_future_acquisition import build_angelone_cash_future_acquisition_service
from .cash_future_universe_pipeline import (
    CashFutureUniversePipelineResult,
    acquire_and_materialize_cash_future_universe,
)
from .cash_future_universe import CashFutureFnoUniverse
from .historical_catalog import HistoricalCatalog
from .historical_ingest import HistoricalIngestionService
from .historical_job_store import HistoricalJobStore
from .provider_retry import ProviderRetryPolicy
from .session_gap_planner import SessionWindow


_ONE_DAY_NS = 86_400_000_000_000


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

    def __post_init__(self) -> None:
        if self.interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        if self.max_request_ns <= 0:
            raise ValueError("max_request_ns must be positive")
        if self.max_request_ns > _ONE_DAY_NS:
            raise ValueError("max_request_ns must not exceed one day")
        if self.chunk_days < 1:
            raise ValueError("chunk_days must be positive")
        if self.retry_attempts < 1:
            raise ValueError("retry_attempts must be positive")
        if self.retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        if self.max_repair_passes < 1:
            raise ValueError("max_repair_passes must be positive")


def run_angelone_cash_future_history(
    *,
    ingestion: HistoricalIngestionService,
    contract_master,
    universe: CashFutureFnoUniverse,
    master_rows: Iterable[Mapping[str, object]],
    start: datetime,
    end: datetime,
    spot_sessions_by_underlying: Mapping[str, tuple[SessionWindow, ...]],
    db,
    catalog: HistoricalCatalog,
    future_sessions_by_instrument: Mapping[str, tuple[SessionWindow, ...]] | None = None,
    job_store: HistoricalJobStore | None = None,
    run_id: str | None = None,
    config: AngelOneCashFutureRunConfig,
    auth: AngelOneAuth | None = None,
    limiter=None,
    retry_policy: ProviderRetryPolicy | None = None,
    on_progress: Callable[[str, object], None] | None = None,
    coverage_store=None,
    margin_required: float = 0.0,
    batch_size: int = 1000,
) -> CashFutureUniversePipelineResult:
    """Acquire bounded Angel One history and materialize it for backtesting."""
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

    return acquire_and_materialize_cash_future_universe(
        service=service,
        universe=universe,
        master_rows=master_rows,
        start=start,
        end=end,
        spot_sessions_by_underlying=spot_sessions_by_underlying,
        future_sessions_by_instrument=future_sessions_by_instrument,
        db=db,
        catalog=catalog,
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
        margin_required=margin_required,
        batch_size=batch_size,
    )


__all__ = ["AngelOneCashFutureRunConfig", "run_angelone_cash_future_history"]
