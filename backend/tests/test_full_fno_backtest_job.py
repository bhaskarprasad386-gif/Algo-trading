from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob
from app.scanner import backtest_jobs


def test_full_fno_job_is_queued_and_persists_future_selection():
    Base.metadata.create_all(bind=engine)
    job = backtest_jobs.create_full_fno_job(days=365, min_entry_gap=0.0, exit_gap=0.0,
                                            charges_per_trade=0.0, funding_cost_per_trade=0.0,
                                            max_holding_days=30, future_selection="BOTH")
    db = SessionLocal()
    try:
        loaded = backtest_jobs.get_job(db, job.job_id)
        assert loaded is not None
        assert loaded.symbol == "__FULL_FNO__"
        assert loaded.contract_month == "BOTH"
        assert loaded.status in {"queued", "running", "completed", "failed"}
    finally:
        backtest_jobs.cancel_job(db, job.job_id)
        db.query(BacktestJob).filter(BacktestJob.job_id == job.job_id).delete()
        db.commit()
        db.close()


def test_full_fno_job_can_be_cancelled():
    Base.metadata.create_all(bind=engine)
    job = backtest_jobs.create_full_fno_job(days=365, min_entry_gap=0.0, exit_gap=0.0,
                                            charges_per_trade=0.0, funding_cost_per_trade=0.0,
                                            max_holding_days=30, future_selection="CURRENT")
    db = SessionLocal()
    try:
        assert backtest_jobs.cancel_job(db, job.job_id) is True
        loaded = backtest_jobs.get_job(db, job.job_id)
        assert loaded is not None
        assert loaded.status == "cancelled"
        assert loaded.contract_month == "CURRENT"
    finally:
        db.query(BacktestJob).filter(BacktestJob.job_id == job.job_id).delete()
        db.commit()
        db.close()
