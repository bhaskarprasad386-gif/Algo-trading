from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.engine import BacktestTrade


def test_backtest_trade_ledger_persists_and_reopens(tmp_path):
    path = tmp_path / "backtest.sqlite"
    trade = BacktestTrade(1, 2, 100.0, 110.0, 1.0, 10.0, 0.0, 10.0)

    with BacktestTradeLedger(path) as ledger:
        assert ledger.append("run-1", 0, (trade,)) == 1
        assert ledger.count("run-1") == 1
        assert ledger.net_pnl("run-1") == 10.0

    with BacktestTradeLedger(path) as reopened:
        assert reopened.count("run-1") == 1
        assert reopened.trades("run-1") == (trade,)


def test_backtest_trade_ledger_is_idempotent_for_replayed_chunk(tmp_path):
    ledger = BacktestTradeLedger(tmp_path / "backtest.sqlite")
    trade = BacktestTrade(1, 2, 100.0, 105.0, 1.0, 5.0, 0.0, 5.0)
    assert ledger.append("run-1", 7, (trade,)) == 1
    assert ledger.append("run-1", 7, (trade,)) == 1
    assert ledger.count("run-1") == 1
    assert ledger.net_pnl("run-1") == 5.0
    ledger.close()


def test_backtest_ledger_persists_run_summary(tmp_path):
    from types import SimpleNamespace

    result = SimpleNamespace(
        initial_capital=1000000.0, final_capital=1012500.0, net_pnl=12500.0,
        total_return=0.0125, win_rate=0.6, expectancy=250.0,
        sharpe_ratio=1.4, sortino_ratio=1.8, max_drawdown=-0.03, cagr=0.15,
    )
    with BacktestTradeLedger(tmp_path / "summary.sqlite") as ledger:
        ledger.save_run("cash-future-1", result)
        summary = ledger.run_summary("cash-future-1")
        assert summary["net_pnl"] == 12500.0
        assert summary["max_drawdown"] == -0.03


def test_backtest_trade_ledger_append_next_allocates_global_run_sequence(tmp_path):
    ledger = BacktestTradeLedger(tmp_path / "sequence.sqlite")
    first = BacktestTrade(1, 2, 100.0, 110.0, 1.0, 10.0, 0.0, 10.0)
    second = BacktestTrade(3, 4, 120.0, 130.0, 1.0, 10.0, 0.0, 10.0)

    assert ledger.next_sequence("run-1") == 0
    assert ledger.append_next("run-1", (first,)) == 1
    assert ledger.next_sequence("run-1") == 1
    assert ledger.append_next("run-1", (second,)) == 1
    assert ledger.next_sequence("run-1") == 2
    assert ledger.trades("run-1") == (first, second)
    assert ledger.count("run-1") == 2
    assert ledger.net_pnl("run-1") == 20.0
    ledger.close()


def test_backtest_trade_ledger_lists_run_ids(tmp_path):
    from types import SimpleNamespace

    result = SimpleNamespace(
        initial_capital=1000.0, final_capital=1020.0, net_pnl=20.0,
        total_return=0.02, win_rate=1.0, expectancy=20.0,
        sharpe_ratio=0.0, sortino_ratio=0.0, max_drawdown=0.0, cagr=0.0,
    )
    with BacktestTradeLedger(tmp_path / "runs.sqlite") as ledger:
        ledger.save_run("run-b", result)
        ledger.save_run("run-a", result)
        assert ledger.run_ids() == ("run-a", "run-b")
