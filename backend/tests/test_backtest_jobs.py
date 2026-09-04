from datetime import datetime

import pytest

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob, BacktestJobResultChunk
from app.scanner import backtest_jobs


def test_full_fno_result_chunk_rejects_minute_ledger_without_persisting():
    Base.metadata.create_all(bind=engine); job_id = "test-job-ledger-rejected"; db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.commit()
        with pytest.raises(ValueError, match="must not contain minute ledger"):
            backtest_jobs._persist_full_fno_chunk(job_id, 0, "RELIANCE", {"net_profit": 1.0, "ledger": [{"minute": 1}]})
        assert backtest_jobs.result_chunk_count(db, job_id) == 0
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete(); db.commit(); db.close()
