"""Durable checkpoint state for historical backtest-data acquisition jobs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BacktestJob


def _state(job: BacktestJob) -> dict[str, Any]:
    if not job.result_json:
        return {}
    try:
        value = json.loads(job.result_json)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def create_historical_sync_job(
    db: Session,
    *,
    job_id: str,
    symbol: str,
    contract_month: str,
    requested_days: int,
    instrument: dict[str, Any],
    start: datetime,
    end: datetime,
) -> BacktestJob:
    """Create a durable acquisition checkpoint, or resume an existing one."""
    job = db.scalar(select(BacktestJob).where(BacktestJob.job_id == job_id))
    if job is None:
        job = BacktestJob(
            job_id=job_id,
            status="queued",
            symbol=symbol,
            contract_month=contract_month,
            requested_days=requested_days,
            result_json=json.dumps(
                {
                    "kind": "historical_sync",
                    "instrument": instrument,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "completed_ranges": 0,
                    "total_ranges": 0,
                    "rows_written": 0,
                },
                sort_keys=True,
            ),
        )
        db.add(job)
    job.status = "running"
    job.message = "historical acquisition started/resumed"
    job.updated_at = datetime.utcnow()
    db.commit()
    return job


def checkpoint_historical_sync_job(
    db: Session,
    *,
    job_id: str,
    completed_ranges: int,
    total_ranges: int,
    rows_written: int,
    status: str = "running",
    message: str | None = None,
) -> BacktestJob:
    """Commit progress after a durable data chunk is stored."""
    job = db.scalar(select(BacktestJob).where(BacktestJob.job_id == job_id))
    if job is None:
        raise ValueError(f"historical sync job not found: {job_id}")
    state = _state(job)
    state.update(
        {
            "completed_ranges": completed_ranges,
            "total_ranges": total_ranges,
            "rows_written": rows_written,
        }
    )
    job.status = status
    job.progress_pct = 100.0 if total_ranges == 0 else min(100.0, completed_ranges * 100.0 / total_ranges)
    job.symbols_processed = completed_ranges
    job.symbols_total = total_ranges
    job.message = message
    job.result_json = json.dumps(state, sort_keys=True)
    job.updated_at = datetime.utcnow()
    db.commit()
    return job


def fail_historical_sync_job(db: Session, *, job_id: str, message: str) -> BacktestJob:
    """Persist a failed state while leaving already imported data resumable."""
    job = db.scalar(select(BacktestJob).where(BacktestJob.job_id == job_id))
    if job is None:
        raise ValueError(f"historical sync job not found: {job_id}")
    job.status = "failed"
    job.message = message
    job.updated_at = datetime.utcnow()
    db.commit()
    return job


def get_historical_sync_job(db: Session, job_id: str) -> BacktestJob | None:
    """Read the latest durable checkpoint for an acquisition job."""
    return db.scalar(select(BacktestJob).where(BacktestJob.job_id == job_id))
