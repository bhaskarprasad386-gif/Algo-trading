from app.backtesting.event_execution import EventExecutionBridge
from app.backtesting.event_strategy import StrategySignal


def test_event_signal_executes_through_shared_simulator():
    result = EventExecutionBridge().execute(
        StrategySignal("BUY", 2, "positive-news"),
        instrument="NIFTY",
        price=100.0,
        timestamp_ns=1_000,
    )
    assert not result.rejected
    assert len(result.fills) == 1
    assert result.fills[0].quantity == 2
    assert result.fills[0].price == 100.0


def test_event_execution_rejects_non_trade_signal():
    result = EventExecutionBridge().execute(
        StrategySignal("HOLD", 1, "no-edge"),
        instrument="NIFTY",
        price=100.0,
        timestamp_ns=1_000,
    )
    assert result.rejected
