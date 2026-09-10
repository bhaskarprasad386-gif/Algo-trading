from pathlib import Path

import pytest

from app.backtesting.backtest_job_store import BacktestJobStore
from app.backtesting.backtest_trade_ledger import BacktestTradeLedger
from app.backtesting.engine import BacktestTrade
from app.backtesting.resumable_backtest import ResumableBacktestRunner


def _trade(value: int) -> BacktestTrade:
    return BacktestTrade(value, value + 1, 100.0, 101.0, 1.0, 1.0, 0.0, 1.0)


def test_durable_restart_recovers_without_manual_start_chunk(tmp_path: Path) -> None:
    ledger = BacktestTradeLedger(tmp_path / "ledger.db")
    store = BacktestJobStore(tmp_path / "jobs.db")
    runner = ResumableBacktestRunner(ledger, chunk_size=2)
    events = tuple(range(6))
    calls: list[tuple[int, ...]] = []

    def fail_on_second(chunk):
        calls.append(tuple(int(value) for value in chunk))
        if chunk == (2, 3):
            raise RuntimeError("simulated failure")
        return ((f"t-{value}", _trade(int(value))) for value in chunk)

    with pytest.raises(RuntimeError, match="simulated failure"):
        runner.run_durable(
            "job-1", "run-1", events, fail_on_second,
            job_store=store, total_chunks=3, plan_parts=("events", "strategy-a"),
        )

    assert calls == [(0, 1), (2, 3)]
    assert ledger.count("job-1") == 2
    assert store.get("job-1")[3] == "failed"

    calls.clear()

    def resume(chunk):
        calls.append(tuple(int(value) for value in chunk))
        return ((f"t-{value}", _trade(int(value))) for value in chunk)

    checkpoint = runner.run_durable(
        "job-1", "run-1", events, resume,
        job_store=store, total_chunks=3, plan_parts=("events", "strategy-a"),
    )

    assert calls == [(2, 3), (4, 5)]
    assert checkpoint.processed_events == 6
    assert checkpoint.chunk_index == 3
    assert ledger.count("job-1") == 6
    assert ledger.net_pnl("job-1") == pytest.approx(6.0)
    assert store.get("job-1")[3] == "completed"
    assert store.get("job-1")[5] == 3


def test_durable_plan_mismatch_is_rejected(tmp_path: Path) -> None:
    ledger = BacktestTradeLedger(tmp_path / "ledger.db")
    store = BacktestJobStore(tmp_path / "jobs.db")
    runner = ResumableBacktestRunner(ledger, chunk_size=2)

    runner.run_durable(
        "job-1", "run-1", (0, 1), lambda chunk: (),
        job_store=store, total_chunks=1, plan_parts=("strategy-a",),
    )

    with pytest.raises(ValueError, match="plan fingerprint"):
        runner.run_durable(
            "job-1", "run-1", (0, 1), lambda chunk: (),
            job_store=store, total_chunks=1, plan_parts=("strategy-b",),
        )


def test_recover_running_chunk_becomes_recoverable(tmp_path: Path) -> None:
    store = BacktestJobStore(tmp_path / "jobs.db")
    store.create("job-1", "run-1", "fingerprint", 1)
    store.start_chunk("job-1", 0)

    assert store.recover_running_chunks("job-1") == 1
    assert store.pending_indices("job-1") == (0,)
