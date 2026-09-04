import json
from datetime import datetime

from sqlalchemy import func, select

from app.backtest.result_store import persist_trade_chunk
from app.backtesting.engine import BacktestTrade
from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJobResultChunk


def _trade(exit_price: float = 101.0) -> BacktestTrade:
    return BacktestTrade(
        entry_timestamp=datetime(2026, 1, 2, 9, 15),
        exit_timestamp=datetime(2026, 1, 2, 9, 16),
        entry_price=100.0,
        exit_price=exit_price,
        quantity=1.0,
        gross_pnl=exit_price - 100.0,
        costs=0.1,
        net_pnl=exit_price - 100.1,
    )


def test_result_chunk_is_durable_json_and_idempotent_by_job_sequence():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    job_id = "test-result-store-job"
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()

        first = persist_trade_chunk(
            db,
            job_id=job_id,
            sequence=0,
            symbol="RELIANCE",
            trades=[_trade()],
        )
        second = persist_trade_chunk(
            db,
            job_id=job_id,
            sequence=0,
            symbol="RELIANCE",
            trades=[_trade(102.0)],
        )

        count = db.scalar(
            select(func.count()).select_from(BacktestJobResultChunk).where(
                BacktestJobResultChunk.job_id == job_id
            )
        )
        payload = json.loads(second.result_json)
        assert first.id == second.id
        assert count == 1
        assert payload[0]["entry_timestamp"] == "2026-01-02T09:15:00"
        assert payload[0]["exit_price"] == 102.0
        assert payload[0]["net_pnl"] == 1.9
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.close()


def test_result_chunk_rejects_empty_or_invalid_identity():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        trade = _trade()
        for kwargs, message in (
            ({"job_id": "", "sequence": 0, "symbol": "RELIANCE"}, "invalid result chunk identity"),
            ({"job_id": "job", "sequence": -1, "symbol": "RELIANCE"}, "invalid result chunk identity"),
            ({"job_id": "job", "sequence": 0, "symbol": "", "trades": [trade]}, "invalid result chunk identity"),
            ({"job_id": "job", "sequence": 0, "symbol": "RELIANCE", "trades": []}, "result chunk cannot be empty"),
        ):
            call_kwargs = dict(kwargs)
            call_kwargs.setdefault("trades", [trade])
            try:
                persist_trade_chunk(db, **call_kwargs)
            except ValueError as exc:
                assert str(exc) == message
            else:
                raise AssertionError("expected result chunk validation")
    finally:
        db.rollback()
        db.close()
