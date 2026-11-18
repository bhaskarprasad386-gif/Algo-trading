import json
from datetime import datetime

from pytest import approx
from sqlalchemy import select

from app.algo.strategy import Strategy, StrategyRule, threshold_rule
from app.backtesting.engine import BacktestEngine, BacktestConfig
from app.backtest.result_store import persist_trade_chunk
from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJobResultChunk


def _strategies():
    entry = Strategy("entry", (StrategyRule("close>=101", threshold_rule("close", minimum=101.0)),))
    exit_ = Strategy("exit", (StrategyRule("close>=102", threshold_rule("close", minimum=102.0)),))
    return entry, exit_


def _candles():
    return [
        {"timestamp": datetime(2026, 1, 2, 9, 15), "close": 100.0},
        {"timestamp": datetime(2026, 1, 2, 9, 16), "close": 101.0},
        {"timestamp": datetime(2026, 1, 2, 9, 17), "close": 102.0},
        {"timestamp": datetime(2026, 1, 2, 9, 18), "close": 103.0},
        {"timestamp": datetime(2026, 1, 2, 9, 19), "close": 104.0},
    ]


def test_incremental_engine_persists_bounded_chunks_and_matches_summary():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    job_id = "incremental-engine-durable-test"
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        entry, exit_ = _strategies()
        config = BacktestConfig(initial_capital=100_000.0, quantity=1.0, transaction_cost_rate=0.001)

        persisted_sequences = []

        def persist(trades, sequence):
            persisted_sequences.append((sequence, len(trades)))
            persist_trade_chunk(db, job_id=job_id, sequence=sequence, symbol="RELIANCE", trades=trades)

        incremental = BacktestEngine(config).run_incremental(
            iter(_candles()), entry, exit_, persist_chunk=persist, chunk_size=1
        )
        regular = BacktestEngine(config).run(_candles(), entry, exit_)

        rows = db.scalars(
            select(BacktestJobResultChunk)
            .where(BacktestJobResultChunk.job_id == job_id)
            .order_by(BacktestJobResultChunk.sequence)
        ).all()
        trades = [trade for row in rows for trade in json.loads(row.result_json)]

        assert persisted_sequences == [(0, 1), (1, 1)]
        assert len(rows) == 2
        assert all(size <= 1 for _, size in persisted_sequences)
        assert len(trades) == 2
        assert incremental.trades == ()
        assert incremental.net_pnl == regular.net_pnl
        assert incremental.final_capital == regular.final_capital
        assert incremental.win_rate == regular.win_rate
        assert incremental.expectancy == approx(regular.expectancy)
        assert incremental.max_drawdown == regular.max_drawdown
        assert sum(float(item["net_pnl"]) for item in trades) == approx(incremental.net_pnl)
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.close()
