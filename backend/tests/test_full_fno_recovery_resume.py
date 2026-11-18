from datetime import datetime

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob, BacktestJobResultChunk
from app.scanner import backtest_jobs


def test_full_fno_recovery_resumes_exact_boundary_and_job(monkeypatch):
    Base.metadata.create_all(bind=engine)
    job_id = "test-recovery-resume"
    other_job_id = "test-recovery-other"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id.in_([job_id, other_job_id])).delete(synchronize_session=False)
        db.query(BacktestJob).filter(BacktestJob.job_id.in_([job_id, other_job_id])).delete(synchronize_session=False)
        db.add(BacktestJob(job_id=job_id, status="running", symbol="__FULL_FNO__", contract_month="BOTH", requested_days=365,
                           progress_pct=40.0, symbols_processed=2, symbols_total=3, message="Worker interrupted",
                           config_json='{"kind":"full_fno","days":365,"min_entry_gap":5.0,"exit_gap":0.0,"charges_per_trade":10.0,"funding_cost_per_trade":2.0,"max_holding_days":30,"future_selection":"BOTH"}',
                           created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
        db.add_all([
            BacktestJobResultChunk(job_id=job_id, sequence=0, symbol="RELIANCE", result_json='{"net_profit":10.0}', created_at=datetime.utcnow()),
            BacktestJobResultChunk(job_id=job_id, sequence=1, symbol="TCS", result_json='{"net_profit":20.0}', created_at=datetime.utcnow()),
            BacktestJobResultChunk(job_id=other_job_id, sequence=2, symbol="INFY", result_json='{"net_profit":999.0}', created_at=datetime.utcnow()),
        ])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(backtest_jobs, "_is_cancelled", lambda _: False)
    captured = {}

    def fake_full_fno(db, **kwargs):
        captured["resume_after_sequence"] = kwargs["resume_after_sequence"]
        captured["durable_job_id"] = kwargs["durable_job_id"]
        kwargs["result_sink"](2, "INFY", {"symbol": "INFY", "net_profit": 30.0})
        return {"status": "completed", "symbols_total": 3, "symbols_processed": 3, "chunks_written": 1,
                "results": None, "net_profit": 60.0, "max_drawdown": 0.0, "completed": 3, "no_entry": 0}

    monkeypatch.setattr(backtest_jobs, "run_full_fno_backtest", fake_full_fno)
    backtest_jobs._run_full_fno_job(job_id, 365, 5.0, 0.0, 10.0, 2.0, 30, "BOTH")

    db = SessionLocal()
    try:
        assert captured == {"resume_after_sequence": 1, "durable_job_id": job_id}
        rows = backtest_jobs.get_result_chunks(db, job_id, after_sequence=None, limit=50)
        assert [row.sequence for row in rows] == [0, 1, 2]
        assert [row.symbol for row in rows] == ["RELIANCE", "TCS", "INFY"]
        assert "999.0" not in rows[2].result_json
        assert backtest_jobs.result_chunk_count(db, other_job_id) == 1
        assert backtest_jobs.get_job(db, job_id).status == "completed"
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id.in_([job_id, other_job_id])).delete(synchronize_session=False)
        db.query(BacktestJob).filter(BacktestJob.job_id.in_([job_id, other_job_id])).delete(synchronize_session=False)
        db.commit()
        db.close()
