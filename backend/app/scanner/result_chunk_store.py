from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import BacktestJobResultChunk


MAX_RESULT_CHUNK_PAGE = 200


def get_result_chunks_after(
    db: Session,
    job_id: str,
    *,
    after_sequence: int | None = None,
    limit: int = 50,
) -> list[BacktestJobResultChunk]:
    """Read result chunks with keyset pagination so deep pages stay efficient."""
    limit = min(max(1, limit), MAX_RESULT_CHUNK_PAGE)
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


def delete_result_chunks_batched(
    db: Session,
    job_id: str,
    *,
    batch_size: int = MAX_RESULT_CHUNK_PAGE,
) -> int:
    """Delete durable result chunks in bounded batches to avoid a large transaction."""
    batch_size = min(max(1, batch_size), MAX_RESULT_CHUNK_PAGE)
    deleted_total = 0
    while True:
        rows = (
            db.query(BacktestJobResultChunk.sequence)
            .filter(BacktestJobResultChunk.job_id == job_id)
            .order_by(BacktestJobResultChunk.sequence)
            .limit(batch_size)
            .all()
        )
        if not rows:
            break

        sequences = [sequence for (sequence,) in rows]
        deleted = db.query(BacktestJobResultChunk).filter(
            BacktestJobResultChunk.job_id == job_id,
            BacktestJobResultChunk.sequence.in_(sequences),
        ).delete(synchronize_session=False)
        db.commit()
        deleted_total += int(deleted or 0)

        if len(sequences) < batch_size:
            break

    return deleted_total
