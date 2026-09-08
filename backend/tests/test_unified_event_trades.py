from app.backtesting.event_execution import EventExecutionBridge
from app.backtesting.event_strategy import StrategySignal
from app.backtesting.unified_runner import UnifiedStrategyRunner


def test_event_execution_fills_can_be_valued_by_unified_runner():
    bridge = EventExecutionBridge()
    entry = bridge.execute(StrategySignal("BUY", 2, "news-entry"), instrument="NIFTY", price=100.0, timestamp_ns=1_000)
    exit_ = bridge.execute(StrategySignal("SELL", 2, "news-exit"), instrument="NIFTY", price=103.0, timestamp_ns=2_000)

    result = UnifiedStrategyRunner().run_event_trades([
        ({"signal": entry.signal, "fill": entry.fills[0]},
         {"signal": exit_.signal, "fill": exit_.fills[0]})
    ])

    assert result.net_pnl == 6.0
    assert result.final_capital == 100_006.0
    assert result.trades == ()
