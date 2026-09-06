"""FastAPI endpoints for durable historical-download progress and job start."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.config import settings

from .cash_future_download_routes import CashFutureDownloadManager, CashFutureDownloadStartRequest
from .download_status_api import download_status_payload
from .historical_download_status import HistoricalDownloadStatusStore


def create_download_status_router(store: HistoricalDownloadStatusStore) -> APIRouter:
    router = APIRouter(prefix="/api/v1/backtesting", tags=["backtesting"])
    manager = CashFutureDownloadManager(
        data_db=settings.BACKTEST_DATA_DB,
        contract_db=settings.BACKTEST_CONTRACT_DB,
        status_store=store,
    )

    @router.post("/cash-future/downloads", status_code=202)
    def start_cash_future_download(request: CashFutureDownloadStartRequest) -> dict:
        return {"job_id": manager.start(request), "status": "QUEUED"}

    @router.get("/cash-future/downloads/{job_id}")
    def get_cash_future_download_status(job_id: str) -> dict:
        try:
            return download_status_payload(store, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="download job not found") from exc

    return router
