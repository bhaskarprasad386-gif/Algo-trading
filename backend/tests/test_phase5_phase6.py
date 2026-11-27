from app.backtesting.execution import (
    DepthLevel,
    ExecutionConfig,
    ExecutionSide,
    ExecutionSimulator,
    OrderBook,
    SimOrder,
)


def test_phase5_multileg_execution_keeps_partial_leg_failure_non_executable():
    simulator = ExecutionSimulator(
        ExecutionConfig(slippage_bps=5, latency_ns=1_000, fee_per_unit=1)
    )
    buy = SimOrder("L1", "OPT-A", ExecutionSide.BUY, 50, submitted_at_ns=10_000)
    sell = SimOrder("L2", "OPT-B", ExecutionSide.SELL, 50, submitted_at_ns=10_000)
    result = simulator.execute_many_atomic(
        [
            (buy, OrderBook(asks=(DepthLevel(100, 50),)), 11_000),
            (sell, OrderBook(bids=(DepthLevel(99, 20),)), 11_000),
        ]
    )
    assert result.rejected
    assert result.reason == "atomic rollback: one or more legs did not fully execute"
    assert result.fills == ()
    assert result.leg_results[0].fills[0].filled_at_ns == 12_000
    assert result.leg_results[1].remaining_quantity == 30
