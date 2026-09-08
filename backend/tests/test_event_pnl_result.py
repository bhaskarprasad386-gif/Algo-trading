from app.backtesting.event_pnl import build_event_trade_pnl
from app.backtesting.event_result import EventResultAggregator
from app.backtesting.execution import ExecutionSide, SimFill


def test_event_trade_net_pnl_includes_execution_fees():
    entry = SimFill("e1", "NIFTY", ExecutionSide.BUY, 2, 100.0, 1000, 0.5)
    exit = SimFill("e2", "NIFTY", ExecutionSide.BUY, 2, 103.0, 2000, 0.5)
    trade = build_event_trade_pnl(entry, exit)
    assert trade.gross_pnl == 6.0
    assert trade.charges == 1.0
    assert trade.net_pnl == 5.0


def test_event_result_aggregates_without_trade_history():
    agg = EventResultAggregator(100.0)
    entry = SimFill("e1", "A", ExecutionSide.BUY, 1, 10.0, 1, 0.0)
    exit = SimFill("e2", "A", ExecutionSide.BUY, 1, 12.0, 2, 0.0)
    agg.add(build_event_trade_pnl(entry, exit))
    result = agg.result()
    assert result.final_capital == 102.0
    assert result.net_pnl == 2.0
    assert result.trades == ()
