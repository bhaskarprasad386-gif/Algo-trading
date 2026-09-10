from pathlib import Path

import pytest

from app.backtesting.backtest_trade_ledger import BacktestTradeLedger
from app.backtesting.engine import BacktestTrade
from app.backtesting.resumable_backtest import ResumableBacktestRunner


def _trade(value: int) -> BacktestTrade:
    return BacktestTrade(value, value + 1, 100.0, 101.0, 1.0, 1.0, 0.0, 1.0)


def test_restart_skips_completed_chunks_and_persists_only_remaining(tmp_path: Path) -> None:
    ledger = BacktestTradeLedger(tmp_path / "ledger.db")
    runner = ResumableBacktestRunner(ledger, chunk_size=2)
    events = tuple(range(6))
    calls: list[tuple[int, ...]] = []

    def execute(chunk: tuple[object, ...]):
        calls.append(tuple(int(value) for value in chunk))
        if chunk == (2, 3):
            raise RuntimeError("simulated failure")
        return ((f"t-{value}", _trade(int(value))) for value in chunk)

    with pytest.raises(RuntimeError, match="simulated failure"):
        runner.run("job-1", events, execute)

    assert calls == [(0, 1), (2, 3)]
    assert ledger.count("job-1") == 2

    calls.clear()

    def resume_execute(chunk: tuple[object, ...]):
        calls.append(tuple(int(value) for value in chunk))
        return ((f"t-{value}", _trade(int(value))) for value in chunk)

    checkpoint = runner.run("job-1", events, resume_execute, start_chunk=1)

    assert calls == [(2, 3), (4, 5)]
    assert checkpoint.chunk_index == 3
    assert checkpoint.processed_events == 6
    assert ledger.count("job-1") == 6
    assert ledger.net_pnl("job-1") == pytest.approx(6.0)


def test_replaying_same_completed_chunk_is_idempotent(tmp_path: Path) -> None:
    ledger = BacktestTradeLedger(tmp_path / "ledger.db")
    runner = ResumableBacktestRunner(ledger, chunk_size=2)
    events = (0, 1)

    def execute(chunk: tuple[object, ...]):
        return ((f"t-{value}", _trade(int(value))) for value in chunk)

    first = runner.run("job-1", events, execute)
    second = runner.run("job-1", events, execute)

    assert first == second
    assert ledger.count("job-1") == 2
    assert ledger.net_pnl("job-1") == pytest.approx(2.0)


def test_invalid_restart_checkpoint_is_rejected(tmp_path: Path) -> None:
    ledger = BacktestTradeLedger(tmp_path / "ledger.db")
    runner = ResumableBacktestRunner(ledger, chunk_size=2)

    with pytest.raises(ValueError, match="start_chunk"):
        runner.run("job-1", (), lambda chunk: (), start_chunk=-1)
