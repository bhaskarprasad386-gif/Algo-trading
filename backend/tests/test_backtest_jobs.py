from datetime import datetime

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob, BacktestJobResultChunk
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


def test_full_fno_chunk_is_durable_across_sessions_and_idempotent():
    """A committed chunk survives a fresh DB session and duplicate delivery is ignored."""
    Base.metadata.create_all(bind=engine)
    job_id = "test-job-durable-chunk"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(
            BacktestJobResultChunk.job_id == job_id,
        ).delete()
        db.commit()

        backtest_jobs._persist_full_fno_chunk(
            job_id, 7, "RELIANCE", {"status": "completed", "net_profit": 123.45}
        )
        backtest_jobs._persist_full_fno_chunk(
            job_id, 7, "RELIANCE", {"status": "completed", "net_profit": 999.0}
        )
        db.expire_all()

        rows = backtest_jobs.get_result_chunks(db, job_id, after_sequence=6, limit=50)
        assert len(rows) == 1
        assert rows[0].sequence == 7
        assert rows[0].symbol == "RELIANCE"
        assert '123.45' in rows[0].result_json
        assert '999.0' not in rows[0].result_json
    finally:
        db.query(BacktestJobResultChunk).filter(
            BacktestJobResultChunk.job_id == job_id,
        ).delete()
        db.commit()
        db.close()


def test_full_fno_result_chunks_use_keyset_paging():
    Base.metadata.create_all(bind=engine)
    job_id = "test-job-keyset-paging"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(
            BacktestJobResultChunk.job_id == job_id,
        ).delete()
        db.commit()
        db.add_all([
            BacktestJobResultChunk(
                job_id=job_id,
                sequence=sequence,
                symbol=f"SYM{sequence}",
                result_json=f'{{"sequence": {sequence}}}',
                created_at=datetime.utcnow(),
            )
            for sequence in range(5)
        ])
        db.commit()

        first = backtest_jobs.get_result_chunks(db, job_id, after_sequence=None, limit=2)
        second = backtest_jobs.get_result_chunks(db, job_id, after_sequence=first[-1].sequence, limit=2)
        assert [row.sequence for row in first] == [0, 1]
        assert [row.sequence for row in second] == [2, 3]
    finally:
        db.query(BacktestJobResultChunk).filter(
            BacktestJobResultChunk.job_id == job_id,
        ).delete()
        db.commit()
        db.close()


def test_full_fno_worker_failure_preserves_already_committed_chunks(monkeypatch):
    """A worker exception after a sink commit must not erase that durable chunk."""
    Base.metadata.create_all(bind=engine)
    job_id = "test-job-worker-failure"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(
            BacktestJobResultChunk.job_id == job_id,
        ).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        db.add(BacktestJob(
            job_id=job_id,
            status="queued",
            symbol="__FULL_FNO__",
            contract_month="BOTH",
            requested_days=365,
            progress_pct=0.0,
            symbols_processed=0,
            symbols_total=1,
            message="Queued",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(backtest_jobs, "_is_cancelled", lambda _: False)

    def fail_after_first_chunk(db, **kwargs):
        kwargs["result_sink"](0, "RELIANCE", {"symbol": "RELIANCE", "net_profit": 123.45})
        raise RuntimeError("simulated worker crash")

    monkeypatch.setattr(backtest_jobs, "run_full_fno_backtest", fail_after_first_chunk)
    backtest_jobs._run_full_fno_job(
        job_id, 365, 5.0, 0.0, 10.0, 2.0, 30, "BOTH"
    )

    db = SessionLocal()
    try:
        job = backtest_jobs.get_job(db, job_id)
        rows = backtest_jobs.get_result_chunks(db, job_id, after_sequence=None, limit=50)
        assert job is not None
        assert job.status == "failed"
        assert len(rows) == 1
        assert rows[0].sequence == 0
        assert rows[0].symbol == "RELIANCE"
        assert "123.45" in rows[0].result_json
    finally:
        db.query(BacktestJobResultChunk).filter(
            BacktestJobResultChunk.job_id == job_id,
        ).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job_id).delete()
        db.commit()
        db.close()
