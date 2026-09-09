from app.backtesting.arbitrage_backtest_suite import STRATEGIES, build_strategy_adapter, strategy_definition
from app.backtesting.arbitrage_strategy_adapters import (
    BoxSpreadStrategyAdapter,
    CalendarSpreadStrategyAdapter,
    CashFutureStrategyAdapter,
    SyntheticCashCarryStrategyAdapter,
)


def test_all_four_arbitrage_strategies_are_first_class_and_distinct():
    assert set(STRATEGIES) == {
        "box-spread",
        "synthetic-cash-carry",
        "cash-future",
        "calendar-spread",
    }
    assert isinstance(build_strategy_adapter("box-spread"), BoxSpreadStrategyAdapter)
    assert isinstance(build_strategy_adapter("synthetic-cash-carry"), SyntheticCashCarryStrategyAdapter)
    assert isinstance(build_strategy_adapter("cash-future"), CashFutureStrategyAdapter)
    assert isinstance(build_strategy_adapter("calendar-spread"), CalendarSpreadStrategyAdapter)


def test_parameters_are_passed_without_cross_strategy_fallback():
    adapter = build_strategy_adapter("box-spread", {"direction": "SHORT", "fees_per_unit": 2.0})
    assert adapter.direction == "SHORT"
    assert adapter.fees_per_unit == 2.0

    try:
        strategy_definition("unknown")
    except ValueError as exc:
        assert "unsupported arbitrage strategy" in str(exc)
    else:
        raise AssertionError("unknown strategy must be rejected")
