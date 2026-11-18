"""Concrete Angel One wiring for durable Cash-Future historical acquisition."""

from __future__ import annotations

from app.algo.auth import AngelOneAuth

from .angelone_historical import AngelOneHistoricalSource
from .cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from .historical_ingest import HistoricalIngestionService
from .historical_rate_limiter import HistoricalRateLimiter


def build_angelone_cash_future_acquisition_service(
    ingestion: HistoricalIngestionService,
    contract_master,
    *,
    interval_ns: int,
    max_request_ns: int,
    auth: AngelOneAuth | None = None,
    limiter: HistoricalRateLimiter | None = None,
    chunk_days: int = 30,
    sleep=None,
) -> CashFutureHistoricalAcquisitionService:
    """Build the production Cash-Future acquisition service on Angel One.

    AngelOneAuth performs server-side TOTP login on first use, while the generic
    acquisition service supplies bounded requests, durable SQLite chunk state,
    retries/backoff, and coverage re-audits. No CSV or synthetic data path is used.
    """
    source = AngelOneHistoricalSource(
        auth=auth,
        limiter=limiter,
        chunk_days=chunk_days,
    )
    return CashFutureHistoricalAcquisitionService(
        ingestion,
        source,
        contract_master,
        interval_ns=interval_ns,
        max_request_ns=max_request_ns,
        sleep=sleep,
    )


__all__ = ["build_angelone_cash_future_acquisition_service"]
