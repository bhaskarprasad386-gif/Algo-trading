from app.backtesting.multi_leg import LegSide, MultiLegSignal, StrategyLeg
from app.backtesting.multi_leg_execution import AtomicMultiLegExecutor


def _signal():
    return MultiLegSignal(
        signal_id="basket-1",
        timestamp_ns=100,
        legs=(
            StrategyLeg("call", "OPT-CE", LegSide.BUY, 1),
            StrategyLeg("put", "OPT-PE", LegSide.SELL, 1),
        ),
    )


def test_atomic_executor_fills_all_legs():
    result = AtomicMultiLegExecutor().execute(_signal(), {"OPT-CE": 10.0, "OPT-PE": 8.0})
    assert not result.rejected
    assert [(fill.instrument, fill.quantity) for fill in result.fills] == [("OPT-CE", 1), ("OPT-PE", 1)]


def test_atomic_executor_rejects_incomplete_basket_without_partial_fills():
    result = AtomicMultiLegExecutor().execute(_signal(), {"OPT-CE": 10.0})
    assert result.rejected
    assert result.fills == ()


def test_atomic_executor_keeps_signal_identity_and_leg_order():
    result = AtomicMultiLegExecutor().execute(_signal(), {"OPT-CE": 10.0, "OPT-PE": 8.0})
    assert result.signal_id == "basket-1"
    assert [fill.order_id for fill in result.fills] == ["basket-1:call", "basket-1:put"]
