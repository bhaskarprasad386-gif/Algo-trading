from app.backtesting.execution import (
    DepthLevel,
    ExecutionSide,
    ExecutionSimulator,
    OrderBook,
    SimOrder,
    TimeInForce,
)


def test_atomic_execution_rejects_partial_ioc_leg():
    simulator = ExecutionSimulator()
    partial_ioc = SimOrder(
        "ioc-leg",
        "NIFTY",
        ExecutionSide.BUY,
        10,
        time_in_force=TimeInForce.IOC,
    )
    full_day = SimOrder(
        "day-leg",
        "BANKNIFTY",
        ExecutionSide.BUY,
        10,
    )
    shallow_book = OrderBook(asks=(DepthLevel(100.0, 5),))
    full_book = OrderBook(asks=(DepthLevel(200.0, 10),))

    result = simulator.execute_many_atomic(
        (
            (partial_ioc, shallow_book, 1),
            (full_day, full_book, 1),
        )
    )

    assert result.rejected is True
    assert result.fills == ()
    assert result.leg_results[0].fills[0].quantity == 5
    assert result.leg_results[0].remaining_quantity == 0
    assert result.leg_results[0].reason == "IOC remainder cancelled"
    assert result.reason == "atomic rollback: one or more legs did not fully execute"


def test_execute_depth_cancels_ioc_remainder_instead_of_leaving_it_resting():
    simulator = ExecutionSimulator()
    order = SimOrder(
        "ioc",
        "NIFTY",
        ExecutionSide.BUY,
        10,
        time_in_force=TimeInForce.IOC,
    )
    result = simulator.execute_depth(order, OrderBook(asks=(DepthLevel(100.0, 5),)), 1)

    assert result.rejected is False
    assert result.fills[0].quantity == 5
    assert result.remaining_quantity == 0
    assert result.reason == "IOC remainder cancelled"
