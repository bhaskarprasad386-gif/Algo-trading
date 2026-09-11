from app.backtesting.cash_future_backtest_result_ledger import (
    RECORD_TYPE,
    CashFutureBacktestResultLedger,
)
from app.backtesting.ledger import BacktestLedger


def _trade():
    return {
        "entry_time": "2026-09-10T09:16:00+05:30",
        "exit_time": "2026-09-10T10:16:00+05:30",
        "entry_gap": 10.0,
        "exit_gap": 4.0,
        "lot_size": 10,
        "gross_profit": 60.0,
        "charges": 5.0,
        "funding_cost": 1.0,
        "net_profit": 54.0,
        "roi_pct": 4.5,
        "exit_reason": "convergence",
    }


def test_cash_future_backtest_trade_is_persisted_durably():
    ledger = BacktestLedger()
    ledger.start_run("cf-1", "cash-future", "1", 100_000)
    writer = CashFutureBacktestResultLedger(ledger, "cf-1")

    writer.append(_trade())

    records = ledger.records("cf-1", RECORD_TYPE)
    assert len(records) == 1
    assert records[0].timestamp_ns > 0
    assert records[0].payload["entry_gap"] == 10.0
    assert records[0].payload["net_profit"] == 54.0
    ledger.close()


def test_cash_future_backtest_trade_batch_is_atomic():
    ledger = BacktestLedger()
    ledger.start_run("cf-2", "cash-future", "1", 100_000)
    writer = CashFutureBacktestResultLedger(ledger, "cf-2")

    assert writer.append_many((_trade(), _trade())) == 2
    assert len(ledger.records("cf-2", RECORD_TYPE)) == 2
    ledger.close()
