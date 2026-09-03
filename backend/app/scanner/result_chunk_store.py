from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import BacktestJobResultChunk


def get_result_chunks_after(
    db: Session,
    job_id: str,
    *,
    after_sequence: int | None = None,
    limit: int = 50,
) -> list[BacktestJobResultChunk]:
    """Read result chunks with keyset pagination so deep pages stay efficient."""
    limit = min(max(1, limit), 200)
    query = db.query(BacktestJobResultChunk).filter(
        BacktestJobResultChunk.job_id == job_id,
    )
    if after_sequence is not None:
        query = query.filter(BacktestJobResultChunk.sequence > after_sequence)
    return query.order_by(BacktestJobResultChunk.sequence).limit(limit).all()


def delete_result_chunks(db: Session, job_id: str) -> int:
    """Delete all durable result chunks for a job and return the deleted count."""
    deleted = db.query(BacktestJobResultChunk).filter(
        BacktestJobResultChunk.job_id == job_id,
    ).delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)
