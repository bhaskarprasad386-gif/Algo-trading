"""Full F&O backtest job routes.

Legacy Cash-Future scanner and historical endpoints have been retired.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.scanner.backtest_jobs import (
    cancel_job,
    create_full_fno_job,
    get_job,
    get_result_chunks,
    result_chunk_count,
)
from app.scanner.result_chunk_store import delete_result_chunks_batched

full_fno_router = APIRouter(
    prefix="/api/v1/backtesting/full-fno",
    tags=["Full F&O Backtest"],
)


class FullFnoJobRequest(BaseModel):
    days: int = Field(365, ge=1, le=3650)
    min_entry_gap: float = 0.0
    exit_gap: float = 0.0
    charges_per_trade: float = Field(0.0, ge=0)
    funding_cost_per_trade: float = Field(0.0, ge=0)
    max_holding_days: int = Field(30, ge=1, le=3650)
    future_selection: str = "BOTH"


@full_fno_router.post("/start")
def start_full_fno_backtest_job_alias(request: FullFnoJobRequest):
    if request.future_selection not in {"CURRENT", "NEAR", "BOTH"}:
        raise HTTPException(status_code=422, detail="future_selection must be CURRENT, NEAR, or BOTH")
    job = create_full_fno_job(**request.model_dump())
    return {
        "status": "accepted",
        "universe": "FULL_FNO_STOCK",
        "future_selection": request.future_selection,
        "job": job.job_id,
    }


@full_fno_router.get("/{job_id}")
def full_fno_job_status_alias(job_id: str, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="backtest job not found")
    return {
        "status": "success",
        "job": {
            "job_id": job.job_id,
            "status": job.status,
            "symbol": job.symbol,
            "contract_month": job.contract_month,
            "requested_days": job.requested_days,
            "progress_pct": job.progress_pct,
            "symbols_processed": job.symbols_processed,
            "symbols_total": job.symbols_total,
            "result_chunks": result_chunk_count(db, job_id),
            "message": job.message,
            "result": json.loads(job.result_json) if job.result_json else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        },
    }


@full_fno_router.post("/{job_id}/cancel")
def full_fno_job_cancel_alias(job_id: str, db: Session = Depends(get_db)):
    if not cancel_job(db, job_id):
        raise HTTPException(status_code=404, detail="backtest job not found or already finished")
    return {"status": "success", "job_id": job_id, "job_status": "cancelled"}


@full_fno_router.get("/{job_id}/results")
def full_fno_job_results_alias(
    job_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    after_sequence: int | None = Query(None, ge=-1),
    db: Session = Depends(get_db),
):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="backtest job not found")
    chunks = get_result_chunks(
        db, job_id, offset=offset, limit=limit, after_sequence=after_sequence
    )
    next_after_sequence = chunks[-1].sequence if chunks else None
    return {
        "status": "success",
        "job_id": job_id,
        "offset": offset,
        "limit": limit,
        "after_sequence": after_sequence,
        "next_after_sequence": next_after_sequence,
        "total": result_chunk_count(db, job_id),
        "data": [
            {
                "sequence": chunk.sequence,
                "symbol": chunk.symbol,
                "result": json.loads(chunk.result_json),
                "created_at": chunk.created_at.isoformat() if chunk.created_at else None,
            }
            for chunk in chunks
        ],
    }


@full_fno_router.delete("/{job_id}/results")
def full_fno_job_purge_alias(job_id: str, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="backtest job not found")
    if job.status not in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="result chunks can only be purged for a terminal job")
    deleted = delete_result_chunks_batched(db, job_id)
    return {
        "status": "success",
        "job_id": job_id,
        "job_status": job.status,
        "deleted_chunks": deleted,
    }
