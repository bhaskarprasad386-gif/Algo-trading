from app.backtesting.cash_future_ledger import CashFutureLedgerWriter
from app.backtesting.cash_future_replay import CashFutureReplayTrade
from app.backtesting.cash_future_pnl import CashFutureTrade
from app.backtesting.ledger import BacktestLedger


def _trade():
    result = CashFutureTrade(100, 105, 102, 96, 2, 10, brokerage=5, funding=3, slippage=2)
    return CashFutureReplayTrade(1, 2, "NFO:101:SBIN26SEP", 10, 2, result)


def test_cash_future_trade_is_persisted_incrementally():
    ledger = BacktestLedger()
    ledger.start_run("run-1", "cash-future", "1", 1_000_000)
    writer = CashFutureLedgerWriter(ledger, "run-1")
    writer.append(_trade())
    records = ledger.records("run-1", "CASH_FUTURE_TRADE")
    assert len(records) == 1
    assert records[0].payload["gross_pnl"] == 220
    assert records[0].payload["net_pnl"] == 210


def test_batch_write_is_durable_and_counted():
    ledger = BacktestLedger()
    ledger.start_run("run-2", "cash-future", "1", 1_000_000)
    writer = CashFutureLedgerWriter(ledger, "run-2")
    assert writer.append_batch([_trade(), _trade()]) == 2
    assert len(ledger.records("run-2", "CASH_FUTURE_TRADE")) == 2
