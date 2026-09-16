import pytest

from app.backtesting.execution import (
    DepthLevel,
    ExecutionSide,
    ExecutionSimulator,
    OrderBook,
    SimOrder,
)


def _buy_order(order_id: str = "o1") -> SimOrder:
    return SimOrder(order_id, "NIFTY", ExecutionSide.BUY, 1)


def test_execute_depth_updates_rejects_out_of_order_timestamps() -> None:
    simulator = ExecutionSimulator()
    book = OrderBook(asks=(DepthLevel(100.0, 1),))

    with pytest.raises(ValueError, match="monotonic"):
        simulator.execute_depth_updates(
            _buy_order(),
            (
                (200, book, ()),
                (100, book, ()),
            ),
        )


def test_execute_depth_updates_allows_equal_timestamps() -> None:
    simulator = ExecutionSimulator()
    book = OrderBook(asks=(DepthLevel(100.0, 1),))

    result = simulator.execute_depth_updates(
        SimOrder("o1", "NIFTY", ExecutionSide.BUY, 2),
        (
            (100, book, ()),
            (100, OrderBook(asks=(DepthLevel(100.0, 1),)), ()),
        ),
    )

    assert not result.rejected
    assert result.remaining_quantity == 0
    assert sum(fill.quantity for fill in result.fills) == 2


def test_atomic_execution_rejects_duplicate_order_ids_before_execution() -> None:
    simulator = ExecutionSimulator()
    book = OrderBook(asks=(DepthLevel(100.0, 1),))
    first = _buy_order("same")
    second = SimOrder("same", "BANKNIFTY", ExecutionSide.BUY, 1)

    result = simulator.execute_many_atomic(
        (
            (first, book, 100),
            (second, book, 100),
        )
    )

    assert result.rejected
    assert result.fills == ()
    assert result.leg_results == ()
    assert result.reason == "atomic transaction contains duplicate order_id"
