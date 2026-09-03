from datetime import date, datetime

from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJob, BacktestJobResultChunk
from app.scanner import backtest_jobs
from app.scanner.cash_future_paper_backtest import PaperBacktestConfig, run_cash_future_paper_backtest
from app.scanner.synchronized_replay import ReplayBar


def test_full_fno_job_is_queued_and_persists_future_selection():
    Base.metadata.create_all(bind=engine)
    job = backtest_jobs.create_full_fno_job(days=365, min_entry_gap=0.0, exit_gap=0.0,
                                            charges_per_trade=0.0, funding_cost_per_trade=0.0,
                                            max_holding_days=30, future_selection="BOTH")
    db = SessionLocal()
    try:
        loaded = backtest_jobs.get_job(db, job.job_id)
        assert loaded is not None
        assert loaded.symbol == "__FULL_FNO__"
        assert loaded.contract_month == "BOTH"
        assert loaded.status in {"queued", "running", "completed", "failed"}
    finally:
        backtest_jobs.cancel_job(db, job.job_id)
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job.job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job.job_id).delete()
        db.commit()
        db.close()


def test_full_fno_job_can_be_cancelled():
    Base.metadata.create_all(bind=engine)
    job = backtest_jobs.create_full_fno_job(days=365, min_entry_gap=0.0, exit_gap=0.0,
                                            charges_per_trade=0.0, funding_cost_per_trade=0.0,
                                            max_holding_days=30, future_selection="CURRENT")
    db = SessionLocal()
    try:
        assert backtest_jobs.cancel_job(db, job.job_id) is True
        loaded = backtest_jobs.get_job(db, job.job_id)
        assert loaded is not None
        assert loaded.status == "cancelled"
        assert loaded.contract_month == "CURRENT"
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job.job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job.job_id).delete()
        db.commit()
        db.close()


def test_full_fno_result_chunks_are_durable_and_bounded():
    Base.metadata.create_all(bind=engine)
    job = BacktestJob(job_id="__chunk_test__", status="running", symbol="__FULL_FNO__",
                      contract_month="BOTH", requested_days=365, progress_pct=50.0,
                      symbols_processed=1, symbols_total=2, message="testing")
    db = SessionLocal()
    try:
        db.add(job)
        db.commit()
        backtest_jobs._persist_full_fno_chunk(job.job_id, 0, "RELIANCE", {"net_profit": 125.0})
        backtest_jobs._persist_full_fno_chunk(job.job_id, 1, "TCS", {"net_profit": -25.0})

        assert backtest_jobs.result_chunk_count(db, job.job_id) == 2
        chunks = backtest_jobs.get_result_chunks(db, job.job_id, offset=0, limit=1)
        assert len(chunks) == 1
        assert chunks[0].sequence == 0
        assert chunks[0].symbol == "RELIANCE"
        assert chunks[0].result_json == '{"net_profit": 125.0}'

        db.expire_all()
        loaded = db.query(BacktestJobResultChunk).filter(
            BacktestJobResultChunk.job_id == job.job_id,
            BacktestJobResultChunk.sequence == 1,
        ).one()
        assert loaded.symbol == "TCS"
        assert loaded.result_json == '{"net_profit": -25.0}'
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job.job_id).delete()
        db.query(BacktestJob).filter(BacktestJob.job_id == job.job_id).delete()
        db.commit()
        db.close()


def test_full_fno_incremental_mode_sinks_each_symbol_without_result_list(monkeypatch):
    from app.scanner import full_fno_backtest

    calls = []

    monkeypatch.setattr(full_fno_backtest, "persisted_stock_symbols",
                        lambda db: ["RELIANCE", "TCS", "INFY"])
    monkeypatch.setattr(full_fno_backtest, "iter_persisted_symbol_replay",
                        lambda db, symbol, start, end: iter([symbol]))

    def fake_engine(bars, config, cancelled=None):
        calls.append((next(iter(bars)), config.collect_ledger, config.future_selection))
        return {"status": "completed", "net_profit": 100.0, "max_drawdown": 0.0}

    monkeypatch.setattr(full_fno_backtest, "run_cash_future_paper_backtest", fake_engine)

    sink_calls = []
    result = full_fno_backtest.run_full_fno_backtest(
        object(),
        days=365,
        min_entry_gap=5.0,
        exit_gap=1.0,
        charges_per_trade=0.0,
        funding_cost_per_trade=0.0,
        max_holding_days=30,
        future_selection="BOTH",
        result_sink=lambda sequence, symbol, item: sink_calls.append((sequence, symbol, item)),
        collect_results=False,
    )

    assert result["status"] == "completed"
    assert result["results"] is None
    assert result["symbols_total"] == 3
    assert result["symbols_processed"] == 3
    assert result["chunks_written"] == 3
    assert [symbol for _, symbol, _ in sink_calls] == ["RELIANCE", "TCS", "INFY"]
    assert [sequence for sequence, _, _ in sink_calls] == [0, 1, 2]
    assert all(collect_ledger is False for _, collect_ledger, _ in calls)
    assert all(selection == "BOTH" for _, _, selection in calls)
    assert result["total_net_profit"] == 300.0


def test_paper_engine_does_not_retain_minute_ledger_when_incremental():
    expiry = date(2026, 9, 30)
    bars = [
        ReplayBar(datetime(2026, 9, 1, 9, 15), 100.0, 105.0, 106.0, expiry, expiry, 10),
        ReplayBar(datetime(2026, 9, 1, 9, 16), 100.0, 100.0, 101.0, expiry, expiry, 10),
    ]
    result = run_cash_future_paper_backtest(
        bars,
        PaperBacktestConfig(min_entry_gap=4.0, exit_gap=0.0, future_selection="CURRENT",
                            collect_ledger=False),
    )
    assert "ledger" not in result
    assert result["status"] == "completed"
