import json
from datetime import datetime

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob
from app.scanner import backtest_jobs


def test_recover_interrupted_full_fno_job_uses_persisted_config(monkeypatch):
    Base.metadata.create_all(bind=engine)
    job_id = "test-recover-full-fno"
    config = {
        "kind": "full_fno",
        "days": 365,
        "min_entry_gap": 5.0,
        "exit_gap": 0.0,
        "charges_per_trade": 10.0,
        "funding_cost_per_trade": 2.0,
        "max_holding_days": 30,
        "future_selection": "BOTH",
    }
    db = SessionLocal()
    try:
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.add(BacktestJob(
            job_id=job_id, status="running", symbol="__FULL_FNO__", contract_month="BOTH",
            requested_days=365, progress_pct=42.0, symbols_processed=42, symbols_total=100,
            message="Interrupted", config_json=json.dumps(config),
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        db.commit()
    finally:
        db.close()

    submitted = {}

    class ImmediateFuture:
        def running(self):
            return False

    def fake_submit(fn, *args):
        submitted["fn"] = fn
        submitted["args"] = args
        return ImmediateFuture()

    monkeypatch.setattr(backtest_jobs._EXECUTOR, "submit", fake_submit)
    try:
        assert backtest_jobs.recover_interrupted_jobs() == 1
        assert submitted["fn"] is backtest_jobs._run_full_fno_job
        assert submitted["args"] == (job_id, 365, 5.0, 0.0, 10.0, 2.0, 30, "BOTH")
        db = SessionLocal()
        job = backtest_jobs.get_job(db, job_id)
        assert job is not None and job.status == "running" and job.message == "Recovery queued from durable configuration"
        db.close()
    finally:
        db = SessionLocal()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        db.close()


def test_recover_interrupted_legacy_job_without_config_fails_safely():
    Base.metadata.create_all(bind=engine)
    job_id = "test-recover-legacy-no-config"
    db = SessionLocal()
    try:
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.add(BacktestJob(
            job_id=job_id, status="running", symbol="__FULL_FNO__", contract_month="BOTH",
            requested_days=365, progress_pct=20.0, symbols_processed=10, symbols_total=100,
            message="Interrupted", config_json=None,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        db.commit()
    finally:
        db.close()

    try:
        assert backtest_jobs.recover_interrupted_jobs() == 0
        db = SessionLocal()
        job = backtest_jobs.get_job(db, job_id)
        assert job is not None and job.status == "failed"
        assert "durable chunks preserved" in job.message
        db.close()
    finally:
        db = SessionLocal()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        db.close()
