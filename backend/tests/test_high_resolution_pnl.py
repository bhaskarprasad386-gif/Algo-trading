from app.backtesting.event_execution import EventExecutionBridge
from app.backtesting.event_strategy import StrategySignal
from app.backtesting.high_resolution_pnl import HighResolutionPositionLedger


def test_high_resolution_buy_sell_preserves_fill_timestamps_and_pnl():
    bridge = EventExecutionBridge()
    buy = bridge.execute(StrategySignal("BUY", 2), instrument="NIFTY", price=100.0, timestamp_ns=1_000_001)
    sell = bridge.execute(StrategySignal("SELL", 2), instrument="NIFTY", price=103.0, timestamp_ns=1_000_009)

    ledger = HighResolutionPositionLedger()
    assert ledger.add(buy) is None
    trade = ledger.add(sell)
    assert trade is not None
    assert trade.entry_timestamp_ns == 1_000_001
    assert trade.exit_timestamp_ns == 1_000_009
    assert trade.gross_pnl == 6.0
    assert ledger.finalize() == 6.0
