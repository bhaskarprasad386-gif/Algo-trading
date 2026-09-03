from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import BacktestJob, BacktestJobResultChunk
from app.scanner.cash_future_backtest import BacktestConfig, run_backtest
from app.scanner.cash_future_history_store import read_history
from app.scanner.full_fno_backtest import run_full_fno_backtest


_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest-worker")
_LOCK = Lock()
_FUTURES: dict[str, Future] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_job_id() -> str:
    import uuid
    return str(uuid.uuid4())


def _update(db: Session, job_id: str, **values) -> None:
    job = db.query(BacktestJob).filter(BacktestJob.job_id == job_id).first()
    if job is None:
        return
    for key, value in values.items():
        setattr(job, key, value)
    job.updated_at = _utcnow()
    db.commit()


def _is_cancelled(job_id: str) -> bool:
    db = SessionLocal()
    try:
        job = db.query(BacktestJob.status).filter(BacktestJob.job_id == job_id).first()
        return job is not None and job[0] == "cancelled"
    finally:
        db.close()


def create_job(*, symbol: str, contract_month: str, days: int, min_entry_gap: float,
               exit_gap: float, charges_per_trade: float, funding_cost_per_trade: float,
               max_holding_days: int) -> BacktestJob:
    db = SessionLocal()
    try:
        job = BacktestJob(job_id=_new_job_id(), status="queued", symbol=symbol.upper(),
                          contract_month=contract_month.upper(), requested_days=days,
                          progress_pct=0.0, symbols_processed=0, symbols_total=1,
                          message="Queued", created_at=_utcnow(), updated_at=_utcnow())
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.job_id
    finally:
        db.close()

    future = _EXECUTOR.submit(_run_job, job_id, symbol.upper(), contract_month.upper(), days,
                              min_entry_gap, exit_gap, charges_per_trade,
                              funding_cost_per_trade, max_holding_days)
    with _LOCK:
        _FUTURES[job_id] = future
    return job


def create_full_fno_job(*, days: int, min_entry_gap: float, exit_gap: float,
                        charges_per_trade: float, funding_cost_per_trade: float,
                        max_holding_days: int, future_selection: str = "BOTH") -> BacktestJob:
    """Queue the full persisted stock-F&O universe without blocking the API/UI."""
    selection = future_selection.upper()
    if selection not in {"CURRENT", "NEAR", "BOTH"}:
        raise ValueError("future_selection must be CURRENT, NEAR or BOTH")

    db = SessionLocal()
    try:
        job = BacktestJob(job_id=_new_job_id(), status="queued", symbol="__FULL_FNO__",
                          contract_month=selection, requested_days=days, progress_pct=0.0,
                          symbols_processed=0, symbols_total=0,
                          message="Queued full F&O backtest", created_at=_utcnow(), updated_at=_utcnow())
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.job_id
    finally:
        db.close()

    future = _EXECUTOR.submit(_run_full_fno_job, job_id, days, min_entry_gap, exit_gap,
                              charges_per_trade, funding_cost_per_trade, max_holding_days, selection)
    with _LOCK:
        _FUTURES[job_id] = future
    return job


def _run_job(job_id: str, symbol: str, contract_month: str, days: int,
             min_entry_gap: float, exit_gap: float, charges_per_trade: float,
             funding_cost_per_trade: float, max_holding_days: int) -> None:
    db = SessionLocal()
    try:
        if _is_cancelled(job_id):
            return
        _update(db, job_id, status="running", progress_pct=1.0, message="Loading validated history")
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        points = read_history(db, symbol, contract_month, start, end)
        if _is_cancelled(job_id):
            return
        _update(db, job_id, progress_pct=20.0, message=f"Loaded {len(points)} historical observations")
        if not points:
            if not _is_cancelled(job_id):
                _update(db, job_id, status="failed", progress_pct=100.0, message="No historical observations found")
            return
        result = run_backtest(points, BacktestConfig(
            min_entry_gap=min_entry_gap,
            exit_gap=exit_gap,
            charges_per_trade=charges_per_trade,
            funding_cost_per_trade=funding_cost_per_trade,
            max_holding_days=max_holding_days,
            contract_month=contract_month,
        ))
        if not _is_cancelled(job_id):
            _update(db, job_id, status="completed", progress_pct=100.0, symbols_processed=1,
                    message="Backtest completed", result_json=json.dumps(result, default=str))
    except Exception as exc:
        if not _is_cancelled(job_id):
            _update(db, job_id, status="failed", progress_pct=100.0, message=str(exc))
    finally:
        db.close()
        with _LOCK:
            _FUTURES.pop(job_id, None)


def _persist_full_fno_chunk(job_id: str, sequence: int, symbol: str, result: dict) -> None:
    """Commit one symbol result immediately and release its JSON from worker memory."""
    db = SessionLocal()
    try:
        existing = db.query(BacktestJobResultChunk).filter(
            BacktestJobResultChunk.job_id == job_id,
            BacktestJobResultChunk.sequence == sequence,
        ).first()
        if existing is None:
            db.add(BacktestJobResultChunk(
                job_id=job_id,
                sequence=sequence,
                symbol=symbol,
                result_json=json.dumps(result, default=str),
                created_at=_utcnow(),
            ))
            db.commit()
    finally:
        db.close()


def _run_full_fno_job(job_id: str, days: int, min_entry_gap: float, exit_gap: float,
                      charges_per_trade: float, funding_cost_per_trade: float,
                      max_holding_days: int, future_selection: str) -> None:
    db = SessionLocal()
    try:
        if _is_cancelled(job_id):
            return
        # Remove stale chunks if a job id is ever retried/reused by an external runner.
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        _update(db, job_id, status="running", progress_pct=1.0,
                message=f"Discovering persisted full F&O coverage ({future_selection})")

        def progress(processed: int, total: int, message: str) -> None:
            if _is_cancelled(job_id):
                return
            _update(db, job_id, progress_pct=100.0 if total == 0 else processed / total * 100.0,
                    symbols_processed=processed, symbols_total=total, message=message)

        result = run_full_fno_backtest(
            db, days=days, min_entry_gap=min_entry_gap, exit_gap=exit_gap,
            charges_per_trade=charges_per_trade, funding_cost_per_trade=funding_cost_per_trade,
            max_holding_days=max_holding_days, future_selection=future_selection,
            progress=progress, cancelled=lambda: _is_cancelled(job_id),
            result_sink=lambda sequence, symbol, item: _persist_full_fno_chunk(job_id, sequence, symbol, item),
            collect_results=False,
        )
        if _is_cancelled(job_id) and result.get("status") != "cancelled":
            return
        status = "cancelled" if result.get("status") == "cancelled" else "completed"
        total = max(result.get("symbols_total", 0), 1)
        # Store only a compact summary in BacktestJob. Detailed per-symbol results
        # live in BacktestJobResultChunk and can be fetched page-by-page.
        summary = {key: value for key, value in result.items() if key != "results"}
        _update(db, job_id, status=status,
                progress_pct=result.get("symbols_processed", 0) / total * 100.0,
                symbols_processed=result.get("symbols_processed", 0),
                symbols_total=result.get("symbols_total", 0),
                message="Full F&O backtest completed" if status == "completed" else "Cancelled",
                result_json=json.dumps(summary, default=str))
    except Exception as exc:
        if not _is_cancelled(job_id):
            _update(db, job_id, status="failed", progress_pct=100.0, message=str(exc))
    finally:
        db.close()
        with _LOCK:
            _FUTURES.pop(job_id, None)


def get_job(db: Session, job_id: str) -> BacktestJob | None:
    return db.query(BacktestJob).filter(BacktestJob.job_id == job_id).first()


def get_result_chunks(db: Session, job_id: str, *, offset: int = 0, limit: int = 50) -> list[BacktestJobResultChunk]:
    """Read durable full-F&O results in bounded pages."""
    offset = max(0, offset)
    limit = min(max(1, limit), 200)
    return db.query(BacktestJobResultChunk).filter(
        BacktestJobResultChunk.job_id == job_id,
    ).order_by(BacktestJobResultChunk.sequence).offset(offset).limit(limit).all()


def result_chunk_count(db: Session, job_id: str) -> int:
    return db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).count()


def cancel_job(db: Session, job_id: str) -> bool:
    job = get_job(db, job_id)
    if job is None or job.status in {"completed", "failed", "cancelled"}:
        return False
    with _LOCK:
        future = _FUTURES.get(job_id)
        if future is not None and not future.running():
            future.cancel()
    job.status = "cancelled"
    job.progress_pct = min(job.progress_pct, 99.0)
    job.message = "Cancelled"
    job.updated_at = _utcnow()
    db.commit()
    return True
