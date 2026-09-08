from app.backtesting.event_strategy import StrategySignal
from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.unified_runner import UnifiedStrategyRunner


def test_event_trade_pair_produces_shared_backtest_result():
    entry = SimFill("entry", "NIFTY", ExecutionSide.BUY, 2, 100.0, 1, 0.5)
    exit_ = SimFill("exit", "NIFTY", ExecutionSide.SELL, 2, 105.0, 2, 0.5)
    result = UnifiedStrategyRunner().run_event_trades([
        ({"signal": StrategySignal("BUY", 2, "news-entry"), "fill": entry},
         {"signal": StrategySignal("SELL", 2, "news-exit"), "fill": exit_})
    ])
    assert result.net_pnl == 9.0
    assert result.final_capital == 100009.0
    assert result.trades == ()
