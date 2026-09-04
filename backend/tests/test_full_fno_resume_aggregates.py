from datetime import datetime

import pytest

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJobResultChunk
from app.scanner.full_fno_backtest import _durable_prefix_aggregates


def test_durable_prefix_aggregates_restore_saved_summary():
    Base.metadata.create_all(bind=engine)
    job_id = "aggregate-helper-isolation"
    db = SessionLocal()
    try:
        # Helper intentionally scopes by the caller's durable prefix rows; use a
        # clean symbol universe and verify the aggregate reconstruction itself.
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.add_all([
            BacktestJobResultChunk(job_id=job_id, sequence=0, symbol="AAA", result_json='{"status":"completed","net_profit":100,"max_drawdown":12}', created_at=datetime.utcnow()),
            BacktestJobResultChunk(job_id=job_id, sequence=1, symbol="BBB", result_json='{"status":"no_entry","net_profit":0,"max_drawdown":3}', created_at=datetime.utcnow()),
        ])
        db.commit()
        # The production helper is sequence-scoped through the job's rows in the
        # worker; emulate that by temporarily binding the rows to a lightweight
        # query wrapper isn't practical here, so validate the same aggregation
        # contract through the public model data below.
        rows = db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).order_by(BacktestJobResultChunk.sequence).all()
        total_profit = sum(float(__import__("json").loads(row.result_json).get("net_profit", 0.0)) for row in rows)
        max_dd = max(float(__import__("json").loads(row.result_json).get("max_drawdown", 0.0)) for row in rows)
        assert [row.symbol for row in rows] == ["AAA", "BBB"]
        assert total_profit == 100.0
        assert max_dd == 12.0
        assert _durable_prefix_aggregates(db, ["AAA", "BBB"], 2) == (100.0, 12.0, 1, 1)
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.close()


def test_durable_prefix_aggregates_rejects_symbol_mismatch():
    Base.metadata.create_all(bind=engine)
    job_id = "aggregate-helper-mismatch"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.add(BacktestJobResultChunk(job_id=job_id, sequence=0, symbol="WRONG", result_json='{"net_profit":1}', created_at=datetime.utcnow()))
        db.commit()
        with pytest.raises(ValueError, match="does not match current symbol universe"):
            _durable_prefix_aggregates(db, ["AAA"], 1)
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.close()
