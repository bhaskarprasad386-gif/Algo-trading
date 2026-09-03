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


def test_full_fno_job_queues_and_persists_job_state(monkeypatch):
    Base.metadata.create_all(bind=engine)
    submitted = {}

    class ImmediateFuture:
        def running(self):
            return False

        def cancel(self):
            return True

    def fake_submit(fn, *args):
        submitted["fn"] = fn
        submitted["args"] = args
        return ImmediateFuture()

    monkeypatch.setattr(backtest_jobs._EXECUTOR, "submit", fake_submit)

    job = backtest_jobs.create_full_fno_job(
        days=365,
        min_entry_gap=5.0,
        exit_gap=0.0,
        charges_per_trade=10.0,
        funding_cost_per_trade=2.0,
        max_holding_days=30,
    )

    db = SessionLocal()
    try:
        loaded = backtest_jobs.get_job(db, job.job_id)
        assert loaded is not None
        assert loaded.status == "queued"
        assert loaded.symbol == "__FULL_FNO__"
        assert loaded.contract_month == "BOTH"
        assert loaded.requested_days == 365
        assert loaded.symbols_processed == 0
        assert loaded.symbols_total == 0
        assert submitted["args"][0] == job.job_id
    finally:
        db.query(BacktestJob).filter(BacktestJob.job_id == job.job_id).delete()
        db.commit()
        db.close()
