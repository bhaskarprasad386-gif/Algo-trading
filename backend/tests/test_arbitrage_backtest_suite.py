"""First-class registry for the retained Box Spread historical strategy."""

from app.backtesting.arbitrage_backtest_suite import (
    STRATEGIES,
    build_strategy_adapter,
    strategy_definition,
    strategy_implementation_hash,
)
from app.backtesting.arbitrage_strategy_adapters import BoxSpreadStrategyAdapter


def test_only_box_spread_is_exposed_by_the_arbitrage_backtest_registry():
    assert set(STRATEGIES) == {"box-spread"}
    assert isinstance(build_strategy_adapter("box-spread"), BoxSpreadStrategyAdapter)


def test_retired_strategies_are_rejected_without_cross_strategy_fallback():
    for strategy_id in ("cash-future", "calendar-spread", "synthetic-cash-carry", "unknown"):
        try:
            strategy_definition(strategy_id)
        except ValueError as exc:
            assert "unsupported arbitrage strategy" in str(exc)
        else:
            raise AssertionError(f"retired strategy must be rejected: {strategy_id}")


def test_box_parameters_and_implementation_hash_remain_stable():
    adapter = build_strategy_adapter("box-spread", {"direction": "SHORT", "fees_per_unit": 2.0})
    assert adapter.direction == "SHORT"
    assert adapter.fees_per_unit == 2.0
    first = strategy_implementation_hash("box-spread")
    second = strategy_implementation_hash("box-spread")
    assert first == second
    assert len(first) == 64
