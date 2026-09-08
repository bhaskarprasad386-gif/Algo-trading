from app.backtesting.event_execution import EventExecutionBridge
from app.backtesting.event_strategy import StrategySignal
from app.backtesting.high_resolution_pnl import HighResolutionPositionLedger
from app.backtesting.high_resolution_ledger import HighResolutionLedgerWriter
from app.backtesting.ledger import BacktestLedger


def test_completed_high_resolution_trades_are_persisted_incrementally():
    bridge = EventExecutionBridge()
    position = HighResolutionPositionLedger()
    buy = bridge.execute(StrategySignal("BUY", 2), instrument="NIFTY", price=100, timestamp_ns=1_000_001)
    sell = bridge.execute(StrategySignal("SELL", 2), instrument="NIFTY", price=103, timestamp_ns=1_000_009)
    position.add(buy)
    trade = position.add(sell)
    assert trade is not None

    ledger = BacktestLedger()
    ledger.start_run("hr", "tick", "1", 100_000)
    HighResolutionLedgerWriter(ledger).append("hr", trade)

    records = ledger.records("hr", "high_resolution_trade")
    assert len(records) == 1
    assert records[0].timestamp_ns == 1_000_009
    assert records[0].payload["net_pnl"] == 6.0
