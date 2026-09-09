from backend.app.backtesting.execution import (
    DepthLevel,
    ExecutionConfig,
    ExecutionSimulator,
    ExecutionSide,
    OrderBook,
    SimOrder,
)


def test_atomic_multi_leg_rolls_back_when_one_leg_has_no_depth():
    simulator = ExecutionSimulator(ExecutionConfig())
    orders = (
        SimOrder("leg-a", "NSE:A", ExecutionSide.BUY, 10),
        SimOrder("leg-b", "NSE:B", ExecutionSide.BUY, 10),
    )
    books = (
        (orders[0], OrderBook(asks=(DepthLevel(100.0, 10),)), 1),
        (orders[1], OrderBook(), 1),
    )

    result = simulator.execute_many_atomic(books)

    assert result.rejected
    assert result.fills == ()
    assert result.leg_results[0].fills
    assert result.leg_results[1].rejected
    assert result.reason == "atomic rollback: one or more legs did not fully execute"


def test_atomic_multi_leg_commits_only_when_all_legs_fully_fill():
    simulator = ExecutionSimulator(ExecutionConfig())
    orders = (
        SimOrder("leg-a", "NSE:A", ExecutionSide.BUY, 10),
        SimOrder("leg-b", "NSE:B", ExecutionSide.SELL, 10),
    )
    books = (
        (orders[0], OrderBook(asks=(DepthLevel(100.0, 10),)), 1),
        (orders[1], OrderBook(bids=(DepthLevel(200.0, 10),)), 1),
    )

    result = simulator.execute_many_atomic(books)

    assert not result.rejected
    assert sum(fill.quantity for fill in result.fills) == 20
    assert all(not leg.rejected and leg.remaining_quantity == 0 for leg in result.leg_results)


def test_atomic_multi_leg_rolls_back_partial_fill():
    simulator = ExecutionSimulator(ExecutionConfig(allow_partial_fills=True))
    orders = (
        SimOrder("leg-a", "NSE:A", ExecutionSide.BUY, 10),
        SimOrder("leg-b", "NSE:B", ExecutionSide.BUY, 10),
    )
    books = (
        (orders[0], OrderBook(asks=(DepthLevel(100.0, 10),)), 1),
        (orders[1], OrderBook(asks=(DepthLevel(200.0, 4),)), 1),
    )

    result = simulator.execute_many_atomic(books)

    assert result.rejected
    assert result.fills == ()
    assert result.leg_results[0].remaining_quantity == 0
    assert result.leg_results[1].remaining_quantity == 6
