import pytest

from app.backtesting.execution import (
    DepthLevel,
    ExecutionConfig,
    ExecutionSide,
    ExecutionSimulator,
    OrderBook,
    OrderType,
    SimOrder,
    TimeInForce,
)


def test_ioc_cancels_residual_from_depth_execution():
    order = SimOrder("ioc", "NIFTY", ExecutionSide.BUY, 5, submitted_at_ns=1, time_in_force=TimeInForce.IOC)
    book = OrderBook(asks=(DepthLevel(100.0, 2),))
    result = ExecutionSimulator().execute_depth(order, book, 2)

    assert [fill.quantity for fill in result.fills] == [2]
    assert result.remaining_quantity == 0
    assert result.reason == "IOC remainder cancelled"
    assert not result.rejected


def test_limit_slippage_never_crosses_limit_price():
    order = SimOrder("limit", "NIFTY", ExecutionSide.BUY, 1, OrderType.LIMIT, limit_price=100.0, submitted_at_ns=1)
    book = OrderBook(asks=(DepthLevel(99.5, 1),))
    result = ExecutionSimulator(ExecutionConfig(slippage_bps=100.0)).execute_depth(order, book, 2)

    assert result.fills == ()
    assert result.rejected
    assert result.remaining_quantity == 1


def test_non_partial_mode_does_not_leak_partial_fill_across_updates():
    order = SimOrder("updates", "NIFTY", ExecutionSide.BUY, 5, submitted_at_ns=1)
    updates = (
        (2, OrderBook(asks=(DepthLevel(100.0, 2),)), ()),
        (3, OrderBook(asks=(DepthLevel(100.0, 2),)), ()),
    )
    result = ExecutionSimulator(ExecutionConfig(allow_partial_fills=False)).execute_depth_updates(order, updates)

    assert result.fills == ()
    assert result.rejected
    assert result.remaining_quantity == 5
    assert result.reason == "insufficient displayed depth across updates"


def test_non_partial_mode_can_fill_only_when_updates_eventually_cover_full_quantity():
    order = SimOrder("updates-full", "NIFTY", ExecutionSide.BUY, 4, submitted_at_ns=1)
    updates = (
        (2, OrderBook(asks=(DepthLevel(100.0, 2),)), ()),
        (3, OrderBook(asks=(DepthLevel(100.0, 4),)), ()),
    )
    result = ExecutionSimulator(ExecutionConfig(allow_partial_fills=False)).execute_depth_updates(order, updates)

    assert [fill.quantity for fill in result.fills] == [2, 2]
    assert result.remaining_quantity == 0
    assert not result.rejected


def test_execution_timestamps_reject_bool_and_float_values():
    order = SimOrder("time", "NIFTY", ExecutionSide.BUY, 1)
    simulator = ExecutionSimulator(ExecutionConfig(latency_ns=5))
    with pytest.raises(ValueError, match="latency_ns"):
        ExecutionConfig(latency_ns=True)
    with pytest.raises(ValueError, match="timestamp_ns"):
        simulator.execute(order, 100.0, 1.5)
    with pytest.raises(ValueError, match="timestamp_ns"):
        simulator.execute_depth(order, OrderBook(asks=(DepthLevel(100.0, 1),)), True)
