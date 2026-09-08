from app.backtesting.event_execution import EventExecutionBridge
from app.backtesting.event_ledger import EventLedgerWriter
from app.backtesting.event_strategy import StrategySignal
from app.backtesting.ledger import BacktestLedger


def test_event_execution_is_persisted_as_durable_ledger_record():
    ledger = BacktestLedger()
    ledger.start_run("run-events", "event", "1", 100_000)
    result = EventExecutionBridge().execute(
        StrategySignal("BUY", 2, "news"), instrument="NIFTY", price=100.0, timestamp_ns=1000
    )
    EventLedgerWriter(ledger).append("run-events", result, 1000)
    records = ledger.records("run-events", "event_execution")
    assert len(records) == 1
    assert records[0].payload["action"] == "BUY"
    assert records[0].payload["fills"][0]["quantity"] == 2
