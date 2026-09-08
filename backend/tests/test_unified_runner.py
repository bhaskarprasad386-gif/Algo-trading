from app.algo.strategy import ThresholdStrategy
from app.backtesting.engine import BacktestConfig
from app.backtesting.multi_leg import LegSide, MultiLegSignal, StrategyLeg
from app.backtesting.multi_leg_pnl import build_basket_pnl
from app.backtesting.unified_runner import UnifiedStrategyRunner


def test_unified_runner_supports_single_leg():
    runner = UnifiedStrategyRunner(BacktestConfig(initial_capital=100, quantity=1))
    result = runner.run_single(
        [{"timestamp": 1, "close": 10}, {"timestamp": 2, "close": 12}, {"timestamp": 3, "close": 13}],
        entry=ThresholdStrategy("close", 10, above=True),
        exit=ThresholdStrategy("close", 12, above=True),
    )
    assert result.net_pnl == 2.0


def test_unified_runner_supports_atomic_multi_leg():
    runner = UnifiedStrategyRunner(BacktestConfig(initial_capital=100))
    signal = MultiLegSignal(
        "basket-1", 123,
        (StrategyLeg("a", "A", LegSide.BUY, 1), StrategyLeg("b", "B", LegSide.SELL, 1)),
    )
    result = runner.run_multi_leg(
        [(signal, {"A": 10, "B": 20}, {"A": 12, "B": 18})],
        pnl_builder=build_basket_pnl,
    )
    assert result.net_pnl == 4.0
    assert result.final_capital == 104.0
