"""FastAPI read-only endpoints for durable historical-download status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .download_status_api import download_status_payload
from .historical_download_status import HistoricalDownloadStatusStore

router = APIRouter(prefix="/api/v1/backtesting", tags=["Backtesting"])

# The application can replace this store with its configured persistent store at startup.
_status_store = HistoricalDownloadStatusStore()


def configure_download_status_store(store: HistoricalDownloadStatusStore) -> None:
    global _status_store
    _status_store = store


@router.get("/cash-future/downloads/{job_id}")
def get_cash_future_download_status(job_id: str):
    try:
        return download_status_payload(_status_store, job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="download job not found") from None
