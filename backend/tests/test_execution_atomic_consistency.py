from backend.app.backtesting.execution import (
    AtomicExecutionResult,
    DepthLevel,
    ExecutionConfig,
    ExecutionSide,
    ExecutionSimulator,
    OrderBook,
    SimOrder,
)


def _order(order_id: str, instrument: str, side: ExecutionSide, quantity: int) -> SimOrder:
    return SimOrder(order_id, instrument, side, quantity)


def test_atomic_multi_leg_full_fill_commits_all_legs() -> None:
    simulator = ExecutionSimulator(ExecutionConfig(fee_per_unit=0.5))
    result = simulator.execute_many_atomic(
        (
            (_order("buy", "NSE:AAA", ExecutionSide.BUY, 10), OrderBook(asks=(DepthLevel(100.0, 10),)), 1_000),
            (_order("sell", "NFO:AAA", ExecutionSide.SELL, 10), OrderBook(bids=(DepthLevel(101.0, 10),)), 1_000),
        )
    )

    assert isinstance(result, AtomicExecutionResult)
    assert not result.rejected
    assert len(result.fills) == 2
    assert [fill.quantity for fill in result.fills] == [10, 10]
    assert all(leg.remaining_quantity == 0 for leg in result.leg_results)


def test_atomic_partial_leg_rolls_back_already_full_leg() -> None:
    simulator = ExecutionSimulator()
    result = simulator.execute_many_atomic(
        (
            (_order("buy", "NSE:AAA", ExecutionSide.BUY, 10), OrderBook(asks=(DepthLevel(100.0, 10),)), 2_000),
            (_order("sell", "NFO:AAA", ExecutionSide.SELL, 10), OrderBook(bids=(DepthLevel(101.0, 4),)), 2_000),
        )
    )

    assert result.rejected
    assert result.fills == ()
    assert result.reason == "atomic rollback: one or more legs did not fully execute"
    assert result.leg_results[0].remaining_quantity == 0
    assert result.leg_results[1].remaining_quantity == 6
    assert result.leg_results[1].fills[0].quantity == 4


def test_atomic_rejection_does_not_mutate_orders_or_reuse_tentative_fills() -> None:
    simulator = ExecutionSimulator()
    buy = _order("buy", "NSE:AAA", ExecutionSide.BUY, 5)
    sell = _order("sell", "NFO:AAA", ExecutionSide.SELL, 5)
    books = (
        (buy, OrderBook(asks=(DepthLevel(100.0, 5),)), 3_000),
        (sell, OrderBook(bids=()), 3_000),
    )

    rejected = simulator.execute_many_atomic(books)
    retry = simulator.execute_many_atomic(
        (
            (buy, OrderBook(asks=(DepthLevel(100.0, 5),)), 4_000),
            (sell, OrderBook(bids=(DepthLevel(101.0, 5),)), 4_000),
        )
    )

    assert rejected.rejected
    assert rejected.fills == ()
    assert retry.rejected is False
    assert sum(fill.quantity for fill in retry.fills) == 10
    assert buy.quantity == 5
    assert sell.quantity == 5
