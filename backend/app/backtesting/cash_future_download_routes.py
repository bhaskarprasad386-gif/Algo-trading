"""Start endpoint for long-running Cash-Future historical downloads."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .cash_future_historical_download import CashFutureHistoricalDownloadService
from .contract_master import ContractMasterCatalog
from .historical_catalog import HistoricalCatalog
from .historical_download_status import HistoricalDownloadStatusStore


class CashFutureDownloadStartRequest(BaseModel):
    spot_instrument: str = Field(min_length=3)
    exchange: str = Field(default="NFO", min_length=2)
    underlying: str = Field(min_length=1)
    start: datetime
    end: datetime
    timeframe: str = "1m"
    mode: str = "BOTH"
    retry_attempts: int = Field(default=3, ge=1, le=10)


class CashFutureDownloadManager:
    """Own persistent catalogs and launch downloads without blocking FastAPI."""

    def __init__(self, *, data_db: str, contract_db: str, status_store: HistoricalDownloadStatusStore) -> None:
        Path(data_db).parent.mkdir(parents=True, exist_ok=True)
        Path(contract_db).parent.mkdir(parents=True, exist_ok=True)
        self.catalog = HistoricalCatalog(data_db)
        self.contract_catalog = ContractMasterCatalog(contract_db)
        self.status_store = status_store
        self._tasks: set[asyncio.Task] = set()

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _validate(self, request: CashFutureDownloadStartRequest) -> tuple[datetime, datetime]:
        start, end = self._utc(request.start), self._utc(request.end)
        if end <= start:
            raise HTTPException(status_code=422, detail="end must be after start")
        if request.timeframe not in {"1m", "3m", "5m", "10m", "15m", "30m", "1h", "1d"}:
            raise HTTPException(status_code=422, detail="unsupported timeframe")
        mode = request.mode.upper()
        if mode not in {"CURRENT", "NEAR", "BOTH"}:
            raise HTTPException(status_code=422, detail="mode must be CURRENT, NEAR or BOTH")
        return start, end

    async def _run(self, job_id: str, request: CashFutureDownloadStartRequest, start: datetime, end: datetime) -> None:
        service = CashFutureHistoricalDownloadService(
            self.catalog, self.contract_catalog, status_store=self.status_store,
        )
        try:
            await asyncio.to_thread(
                service.run,
                spot_instrument=request.spot_instrument.strip(),
                exchange=request.exchange.strip().upper(),
                underlying=request.underlying.strip().upper(),
                start=start,
                end=end,
                timeframe=request.timeframe,
                mode=request.mode.upper(),
                retry_attempts=request.retry_attempts,
                job_id=job_id,
            )
            job = self.status_store.job(job_id)
            if job is not None and job.status not in {"COMPLETE", "FAILED"}:
                self.status_store.update_job(job_id, status="COMPLETE", catalog_count=self.catalog.count())
        except Exception as exc:
            if self.status_store.job(job_id) is not None:
                self.status_store.update_job(job_id, status="FAILED", error=str(exc), catalog_count=self.catalog.count())

    def start(self, request: CashFutureDownloadStartRequest) -> str:
        start, end = self._validate(request)
        job_id = uuid.uuid4().hex
        # The service fills the exact chunk count after resolving historical contracts.
        self.status_store.create_job(
            job_id=job_id, mode=request.mode.upper(), timeframe=request.timeframe,
            spot_instrument=request.spot_instrument.strip(), exchange=request.exchange.strip().upper(),
            underlying=request.underlying.strip().upper(),
            start_ns=int(start.timestamp() * 1_000_000_000),
            end_ns=int(end.timestamp() * 1_000_000_000), requested_chunks=0,
        )
        task = asyncio.create_task(self._run(job_id, request, start, end))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job_id

    def close(self) -> None:
        self.catalog.close()
        self.contract_catalog.close()


def create_cash_future_download_router(manager: CashFutureDownloadManager) -> APIRouter:
    router = APIRouter(prefix="/api/v1/backtesting", tags=["backtesting"])

    @router.post("/cash-future/downloads", status_code=202)
    def start_cash_future_download(request: CashFutureDownloadStartRequest) -> dict:
        return {"job_id": manager.start(request), "status": "QUEUED"}

    return router
