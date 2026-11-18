from concurrent.futures import Future
from datetime import datetime

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob
from app.scanner import backtest_jobs


def test_recovery_does_not_duplicate_active_full_fno_worker(monkeypatch):
    Base.metadata.create_all(bind=engine)
    job_id = "test-recovery-active"
    db = SessionLocal()
    try:
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete(synchronize_session=False)
        db.add(BacktestJob(
            job_id=job_id,
            status="running",
            symbol="__FULL_FNO__",
            contract_month="BOTH",
            requested_days=365,
            progress_pct=50.0,
            symbols_processed=1,
            symbols_total=2,
            message="Already running",
            config_json='{"kind":"full_fno","days":365,"min_entry_gap":5.0,"exit_gap":0.0,"charges_per_trade":10.0,"funding_cost_per_trade":2.0,"max_holding_days":30,"future_selection":"BOTH"}',
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
        db.commit()
    finally:
        db.close()

    active = Future()
    backtest_jobs._FUTURES[job_id] = active
    monkeypatch.setattr(backtest_jobs, "_EXECUTOR", None)
    try:
        assert backtest_jobs.recover_interrupted_jobs() == 0
        db = SessionLocal()
        try:
            job = backtest_jobs.get_job(db, job_id)
            assert job is not None
            assert job.status == "running"
            assert job.message == "Already running"
        finally:
            db.close()
    finally:
        backtest_jobs._FUTURES.pop(job_id, None)
        db = SessionLocal()
        try:
            db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
