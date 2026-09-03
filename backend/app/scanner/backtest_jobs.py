from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import BacktestJob
from app.scanner.cash_future_backtest import BacktestConfig, run_backtest
from app.scanner.cash_future_history_store import read_history


# Bounded worker: large replays must not consume every available worker and
# starve lightweight API/UI operations. The queue contract allows a later
# process-backed executor without changing clients.
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


def create_job(
    *,
    symbol: str,
    contract_month: str,
    days: int,
    min_entry_gap: float,
    exit_gap: float,
    charges_per_trade: float,
    funding_cost_per_trade: float,
    max_holding_days: int,
) -> BacktestJob:
    db = SessionLocal()
    try:
        job = BacktestJob(
            job_id=_new_job_id(),
            status="queued",
            symbol=symbol.upper(),
            contract_month=contract_month.upper(),
            requested_days=days,
            progress_pct=0.0,
            symbols_processed=0,
            symbols_total=1,
            message="Queued",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.job_id
    finally:
        db.close()

    future = _EXECUTOR.submit(
        _run_job,
        job_id,
        symbol.upper(),
        contract_month.upper(),
        days,
        min_entry_gap,
        exit_gap,
        charges_per_trade,
        funding_cost_per_trade,
        max_holding_days,
    )
    with _LOCK:
        _FUTURES[job_id] = future
    return job


def _run_job(
    job_id: str,
    symbol: str,
    contract_month: str,
    days: int,
    min_entry_gap: float,
    exit_gap: float,
    charges_per_trade: float,
    funding_cost_per_trade: float,
    max_holding_days: int,
) -> None:
    db = SessionLocal()
    try:
        _update(db, job_id, status="running", progress_pct=1.0, message="Loading validated history")
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        points = read_history(db, symbol, contract_month, start, end)
        _update(db, job_id, progress_pct=20.0, message=f"Loaded {len(points)} historical observations")
        if not points:
            _update(db, job_id, status="failed", progress_pct=100.0, message="No historical observations found")
            return

        result = run_backtest(
            points,
            BacktestConfig(
                min_entry_gap=min_entry_gap,
                exit_gap=exit_gap,
                charges_per_trade=charges_per_trade,
                funding_cost_per_trade=funding_cost_per_trade,
                max_holding_days=max_holding_days,
                contract_month=contract_month,
            ),
        )
        _update(
            db,
            job_id,
            status="completed",
            progress_pct=100.0,
            symbols_processed=1,
            message="Backtest completed",
            result_json=str(result),
        )
    except Exception as exc:
        _update(db, job_id, status="failed", progress_pct=100.0, message=str(exc))
    finally:
        db.close()
        with _LOCK:
            _FUTURES.pop(job_id, None)


def get_job(db: Session, job_id: str) -> BacktestJob | None:
    return db.query(BacktestJob).filter(BacktestJob.job_id == job_id).first()


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
