from pathlib import Path

import pytest

from app.backtesting.backtest_trade_ledger import BacktestTradeLedger
from app.backtesting.engine import BacktestTrade


def _trade(entry: int, exit: int, pnl: float) -> BacktestTrade:
    return BacktestTrade(entry, exit, 100.0, 100.0 + pnl, 1.0, pnl, 0.0, pnl)


def test_ledger_chunk_is_idempotent(tmp_path: Path) -> None:
    ledger = BacktestTradeLedger(tmp_path / "ledger.db")
    trades = (_trade(1, 2, 10.0), _trade(3, 4, -2.0))

    assert ledger.append_chunk("job-1", ("t1", "t2"), trades) == 2
    assert ledger.append_chunk("job-1", ("t1", "t2"), trades) == 0
    assert ledger.count("job-1") == 2
    assert ledger.net_pnl("job-1") == pytest.approx(8.0)


def test_ledger_rejects_conflicting_replay(tmp_path: Path) -> None:
    ledger = BacktestTradeLedger(tmp_path / "ledger.db")
    ledger.append_chunk("job-1", ("t1",), (_trade(1, 2, 10.0),))

    with pytest.raises(ValueError, match="conflicting trade replay"):
        ledger.append_chunk("job-1", ("t1",), (_trade(1, 2, 11.0),))


def test_ledger_keeps_jobs_isolated(tmp_path: Path) -> None:
    ledger = BacktestTradeLedger(tmp_path / "ledger.db")
    ledger.append_chunk("job-1", ("t1",), (_trade(1, 2, 10.0),))
    ledger.append_chunk("job-2", ("t1",), (_trade(1, 2, 20.0),))

    assert ledger.count("job-1") == 1
    assert ledger.count("job-2") == 1
    assert ledger.net_pnl("job-1") == pytest.approx(10.0)
    assert ledger.net_pnl("job-2") == pytest.approx(20.0)
