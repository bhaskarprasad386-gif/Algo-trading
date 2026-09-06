"""FastAPI endpoints for durable historical-download progress."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .cash_future_download_routes import CashFutureDownloadManager
from .download_status_api import download_status_payload
from .historical_download_status import HistoricalDownloadStatusStore


def create_download_status_router(
    store: HistoricalDownloadStatusStore,
    manager: CashFutureDownloadManager | None = None,
) -> APIRouter:
    """Create status-only routes, optionally retaining backward-compatible start access."""
    router = APIRouter(prefix="/api/v1/backtesting", tags=["backtesting"])

    @router.get("/cash-future/downloads/{job_id}")
    def get_cash_future_download_status(job_id: str) -> dict:
        try:
            return download_status_payload(store, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="download job not found") from exc

    return router
