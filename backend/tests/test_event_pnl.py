from app.backtesting.event_pnl import build_event_trade_pnl
from app.backtesting.execution import ExecutionSide, SimFill


def test_event_trade_pnl_uses_execution_fees():
    entry = SimFill("e", "NIFTY", ExecutionSide.BUY, 2, 100.0, 1000, 0.2)
    exit = SimFill("e", "NIFTY", ExecutionSide.BUY, 2, 105.0, 2000, 0.3)
    trade = build_event_trade_pnl(entry, exit)
    assert trade.gross_pnl == 10.0
    assert trade.charges == 0.5
    assert trade.net_pnl == 9.5
