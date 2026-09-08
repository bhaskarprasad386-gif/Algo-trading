from app.backtesting.event_pnl import EventTradePnl
from app.backtesting.event_result import EventResultAggregator
from app.backtesting.execution import ExecutionSide


def test_event_result_aggregates_net_pnl_into_shared_result():
    agg = EventResultAggregator(100.0)
    agg.add(EventTradePnl("NIFTY", 2, ExecutionSide.BUY, 100.0, 103.0, 0.0, 0.0))
    agg.add(EventTradePnl("NIFTY", 1, ExecutionSide.BUY, 103.0, 102.0, 0.0, 0.0))
    result = agg.result()
    assert result.final_capital == 105.0
    assert result.net_pnl == 5.0
    assert result.win_rate == 0.5


def test_event_result_supports_incremental_add_many():
    agg = EventResultAggregator(100.0)
    trades = [EventTradePnl("A", 1, ExecutionSide.BUY, 10.0, 11.0, 0.1, 0.1)]
    agg.add_many(trades)
    assert agg.result().net_pnl == 0.8
