from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.multi_leg_pnl import build_basket_pnl
from app.backtesting.basket_result import BasketResultAggregator


def _basket(signal_id: str, entry: float, exit_: float):
    entries = {f"{signal_id}:a": SimFill(f"{signal_id}:a", "A", ExecutionSide.BUY, 1, entry, 1)}
    exits = {f"{signal_id}:a": SimFill(f"{signal_id}:a", "A", ExecutionSide.BUY, 1, exit_, 2)}
    return build_basket_pnl(signal_id, entries, exits)


def test_basket_results_feed_shared_backtest_result():
    agg = BasketResultAggregator(100.0)
    agg.add(_basket("b1", 10.0, 12.0), 100)
    agg.add(_basket("b2", 12.0, 11.0), 200)

    result = agg.result()
    assert result.initial_capital == 100.0
    assert result.final_capital == 101.0
    assert result.net_pnl == 1.0
    assert result.win_rate == 0.5
    assert result.expectancy == 0.5
    assert result.max_drawdown == 0.01
    assert result.trades == ()
