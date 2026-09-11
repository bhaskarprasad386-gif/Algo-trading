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
