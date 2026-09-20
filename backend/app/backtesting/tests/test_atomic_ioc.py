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
    assert result.leg_results[0].remaining_quantity == 5
    assert result.reason == "atomic rollback: one or more legs did not fully execute"
