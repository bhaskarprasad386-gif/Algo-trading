import json
from datetime import datetime

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob, BacktestJobResultChunk
from app.scanner import backtest_jobs


def test_recover_interrupted_full_fno_job_requeues_from_persisted_config(monkeypatch):
    Base.metadata.create_all(bind=engine)
    job_id = "test-job-recover-interrupted"
    submitted = {}

    class QueuedFuture:
        def done(self):
            return False

    def fake_submit(fn, *args):
        submitted["fn"] = fn
        submitted["args"] = args
        return QueuedFuture()

    monkeypatch.setattr(backtest_jobs._EXECUTOR, "submit", fake_submit)
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        db.add(
            BacktestJob(
                job_id=job_id,
                status="running",
                symbol="__FULL_FNO__",
                contract_month="BOTH",
                requested_days=365,
                progress_pct=37.5,
                symbols_processed=3,
                symbols_total=10,
                message="Worker interrupted",
                config_json=json.dumps(
                    {
                        "kind": "full_fno",
                        "days": 365,
                        "min_entry_gap": 5.0,
                        "exit_gap": 0.0,
                        "charges_per_trade": 10.0,
                        "funding_cost_per_trade": 2.0,
                        "max_holding_days": 30,
                        "future_selection": "BOTH",
                    }
                ),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        db.add(
            BacktestJobResultChunk(
                job_id=job_id,
                sequence=0,
                symbol="RELIANCE",
                result_json='{"net_profit": 123.45}',
                created_at=datetime.utcnow(),
            )
        )
        db.commit()
    finally:
        db.close()

    assert backtest_jobs.recover_interrupted_jobs() == 1
    assert submitted["args"] == (job_id, 365, 5.0, 0.0, 10.0, 2.0, 30, "BOTH")

    db = SessionLocal()
    try:
        job = backtest_jobs.get_job(db, job_id)
        rows = backtest_jobs.get_result_chunks(db, job_id, after_sequence=None, limit=50)
        assert job is not None
        assert job.status == "running"
        assert job.message == "Recovery queued from durable configuration"
        assert len(rows) == 1
        assert rows[0].sequence == 0
        assert rows[0].symbol == "RELIANCE"
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        db.close()
