from datetime import datetime

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob
from app.scanner import backtest_jobs


def test_backtest_job_state_is_persisted_and_cancellable():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        job = BacktestJob(
            job_id="test-job-cancel",
            status="queued",
            symbol="TEST",
            contract_month="CURRENT",
            requested_days=365,
            progress_pct=0.0,
            symbols_processed=0,
            symbols_total=1,
            message="Queued",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(job)
        db.commit()

        loaded = backtest_jobs.get_job(db, "test-job-cancel")
        assert loaded is not None
        assert loaded.status == "queued"
        assert loaded.progress_pct == 0.0

        assert backtest_jobs.cancel_job(db, "test-job-cancel") is True
        loaded = backtest_jobs.get_job(db, "test-job-cancel")
        assert loaded.status == "cancelled"
        assert loaded.message == "Cancelled"
    finally:
        db.query(BacktestJob).filter(BacktestJob.job_id == "test-job-cancel").delete()
        db.commit()
        db.close()
