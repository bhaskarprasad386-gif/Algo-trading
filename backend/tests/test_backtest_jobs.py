from datetime import datetime

import pytest

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob, BacktestJobResultChunk
from app.scanner import backtest_jobs


def test_backtest_job_state_is_persisted_and_cancellable():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        job = BacktestJob(job_id="test-job-cancel", status="queued", symbol="TEST", contract_month="CURRENT", requested_days=365, progress_pct=0.0, symbols_processed=0, symbols_total=1, message="Queued", created_at=datetime.utcnow(), updated_at=datetime.utcnow())
        db.add(job); db.commit()
        loaded = backtest_jobs.get_job(db, "test-job-cancel")
        assert loaded is not None and loaded.status == "queued" and loaded.progress_pct == 0.0
        assert backtest_jobs.cancel_job(db, "test-job-cancel") is True
        loaded = backtest_jobs.get_job(db, "test-job-cancel")
        assert loaded.status == "cancelled" and loaded.message == "Cancelled"
    finally:
        db.query(BacktestJob).filter(BacktestJob.job_id == "test-job-cancel").delete(); db.commit(); db.close()


def test_full_fno_job_queues_and_persists_job_state(monkeypatch):
    Base.metadata.create_all(bind=engine); submitted = {}
    class ImmediateFuture:
        def running(self): return False
        def cancel(self): return True
    def fake_submit(fn, *args): submitted["fn"] = fn; submitted["args"] = args; return ImmediateFuture()
    monkeypatch.setattr(backtest_jobs._EXECUTOR, "submit", fake_submit)
    job = backtest_jobs.create_full_fno_job(days=365, min_entry_gap=5.0, exit_gap=0.0, charges_per_trade=10.0, funding_cost_per_trade=2.0, max_holding_days=30)
    db = SessionLocal()
    try:
        loaded = backtest_jobs.get_job(db, job.job_id)
        assert loaded is not None and loaded.status == "queued" and loaded.symbol == "__FULL_FNO__" and loaded.contract_month == "BOTH" and loaded.requested_days == 365 and loaded.symbols_processed == 0 and loaded.symbols_total == 0
        assert submitted["args"][0] == job.job_id
    finally:
        db.query(BacktestJob).filter(BacktestJob.job_id == job.job_id).delete(); db.commit(); db.close()


def test_full_fno_chunk_is_durable_across_sessions_and_idempotent():
    Base.metadata.create_all(bind=engine); job_id = "test-job-durable-chunk"; db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.commit()
        backtest_jobs._persist_full_fno_chunk(job_id, 7, "RELIANCE", {"status": "completed", "net_profit": 123.45})
        backtest_jobs._persist_full_fno_chunk(job_id, 7, "RELIANCE", {"status": "completed", "net_profit": 999.0})
        db.expire_all(); rows = backtest_jobs.get_result_chunks(db, job_id, after_sequence=6, limit=50)
        assert len(rows) == 1 and rows[0].sequence == 7 and rows[0].symbol == "RELIANCE" and '123.45' in rows[0].result_json and '999.0' not in rows[0].result_json
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.commit(); db.close()


def test_full_fno_result_chunk_rejects_invalid_payload_without_persisting():
    Base.metadata.create_all(bind=engine); job_id = "test-job-invalid-chunk"; db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.commit()
        with pytest.raises(ValueError):
            backtest_jobs._persist_full_fno_chunk(job_id, -1, "RELIANCE", {})
        with pytest.raises(ValueError):
            backtest_jobs._persist_full_fno_chunk("", 0, "RELIANCE", {})
        with pytest.raises(TypeError):
            backtest_jobs._persist_full_fno_chunk(job_id, 0, "RELIANCE", ["bad"])
        assert backtest_jobs.result_chunk_count(db, job_id) == 0
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.commit(); db.close()


def test_full_fno_result_chunks_use_keyset_paging():
    Base.metadata.create_all(bind=engine); job_id = "test-job-keyset-paging"; db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.commit()
        db.add_all([BacktestJobResultChunk(job_id=job_id, sequence=s, symbol=f"SYM{s}", result_json=f'{{"sequence": {s}}}', created_at=datetime.utcnow()) for s in range(5)]); db.commit()
        first = backtest_jobs.get_result_chunks(db, job_id, after_sequence=None, limit=2); second = backtest_jobs.get_result_chunks(db, job_id, after_sequence=first[-1].sequence, limit=2)
        assert [row.sequence for row in first] == [0, 1] and [row.sequence for row in second] == [2, 3]
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.commit(); db.close()


def test_full_fno_worker_failure_preserves_already_committed_chunks(monkeypatch):
    Base.metadata.create_all(bind=engine); job_id = "test-job-worker-failure"; db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete(); db.commit()
        db.add(BacktestJob(job_id=job_id, status="queued", symbol="__FULL_FNO__", contract_month="BOTH", requested_days=365, progress_pct=0.0, symbols_processed=0, symbols_total=1, message="Queued", created_at=datetime.utcnow(), updated_at=datetime.utcnow())); db.commit()
    finally: db.close()
    monkeypatch.setattr(backtest_jobs, "_is_cancelled", lambda _: False)
    def fail_after_first_chunk(db, **kwargs):
        kwargs["result_sink"](0, "RELIANCE", {"symbol": "RELIANCE", "net_profit": 123.45}); raise RuntimeError("simulated worker crash")
    monkeypatch.setattr(backtest_jobs, "run_full_fno_backtest", fail_after_first_chunk); backtest_jobs._run_full_fno_job(job_id, 365, 5.0, 0.0, 10.0, 2.0, 30, "BOTH")
    db = SessionLocal()
    try:
        job = backtest_jobs.get_job(db, job_id); rows = backtest_jobs.get_result_chunks(db, job_id, after_sequence=None, limit=50)
        assert job is not None and job.status == "failed" and len(rows) == 1 and rows[0].sequence == 0 and rows[0].symbol == "RELIANCE" and "123.45" in rows[0].result_json
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete(); db.commit(); db.close()


def test_full_fno_cancellation_does_not_persist_current_symbol_result(monkeypatch):
    """Cancellation returned by the full runner is persisted as cancelled without adding later chunks."""
    Base.metadata.create_all(bind=engine); job_id = "test-job-cancel-during-replay"; db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete(); db.commit()
        db.add(BacktestJob(job_id=job_id, status="queued", symbol="__FULL_FNO__", contract_month="BOTH", requested_days=365, progress_pct=0.0, symbols_processed=0, symbols_total=2, message="Queued", created_at=datetime.utcnow(), updated_at=datetime.utcnow())); db.commit()
    finally: db.close()
    monkeypatch.setattr(backtest_jobs, "_is_cancelled", lambda _: False)
    def fake_full(db, **kwargs):
        kwargs["result_sink"](0, "RELIANCE", {"symbol": "RELIANCE", "net_profit": 10.0})
        return {"status": "cancelled", "symbols_total": 2, "symbols_processed": 0, "chunks_written": 1, "results": None}
    monkeypatch.setattr(backtest_jobs, "run_full_fno_backtest", fake_full)
    backtest_jobs._run_full_fno_job(job_id, 365, 5.0, 0.0, 10.0, 2.0, 30, "BOTH")
    db = SessionLocal()
    try:
        rows = backtest_jobs.get_result_chunks(db, job_id, after_sequence=None, limit=50); job = backtest_jobs.get_job(db, job_id)
        assert job is not None and job.status == "cancelled" and len(rows) == 1 and rows[0].symbol == "RELIANCE"
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete(); db.commit(); db.close()
