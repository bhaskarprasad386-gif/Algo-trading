from datetime import datetime

import pytest
from fastapi import HTTPException

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob, BacktestJobResultChunk
from app.scanner import routes


def _seed_job(db, job_id: str, status: str, chunk_count: int = 3) -> None:
    db.add(
        BacktestJob(
            job_id=job_id,
            status=status,
            symbol="__FULL_FNO__",
            contract_month="BOTH",
            requested_days=365,
            progress_pct=100.0 if status == "completed" else 0.0,
            symbols_processed=1,
            symbols_total=1,
            message=status,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    db.add_all(
        [
            BacktestJobResultChunk(
                job_id=job_id,
                sequence=sequence,
                symbol=f"SYM{sequence}",
                result_json=f'{{"sequence": {sequence}}}',
                created_at=datetime.utcnow(),
            )
            for sequence in range(chunk_count)
        ]
    )
    db.commit()


def test_result_cleanup_api_deletes_terminal_job_chunks_in_batches():
    Base.metadata.create_all(bind=engine)
    job_id = "test-api-result-cleanup"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        _seed_job(db, job_id, "completed")

        result = routes.purge_cash_future_backtest_job_results(job_id, db)

        assert result == {
            "status": "success",
            "job_id": job_id,
            "job_status": "completed",
            "deleted_chunks": 3,
        }
        assert routes.result_chunk_count(db, job_id) == 0
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        db.close()


def test_result_cleanup_api_handles_more_than_one_bounded_batch():
    Base.metadata.create_all(bind=engine)
    job_id = "test-api-result-cleanup-large"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        _seed_job(db, job_id, "completed", chunk_count=405)

        result = routes.purge_cash_future_backtest_job_results(job_id, db)

        assert result["deleted_chunks"] == 405
        assert routes.result_chunk_count(db, job_id) == 0
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        db.close()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_result_cleanup_api_rejects_non_terminal_job(status):
    Base.metadata.create_all(bind=engine)
    job_id = f"test-api-result-cleanup-{status}"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        _seed_job(db, job_id, status)

        with pytest.raises(HTTPException) as exc:
            routes.purge_cash_future_backtest_job_results(job_id, db)

        assert exc.value.status_code == 409
        assert routes.result_chunk_count(db, job_id) == 3
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        db.close()


def test_result_cleanup_api_returns_404_for_unknown_job():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as exc:
            routes.purge_cash_future_backtest_job_results("missing-result-cleanup-job", db)
        assert exc.value.status_code == 404
    finally:
        db.close()
