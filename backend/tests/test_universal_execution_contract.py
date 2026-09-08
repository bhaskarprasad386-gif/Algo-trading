from app.backtesting.execution import (
    DepthLevel,
    ExecutionConfig,
    ExecutionSide,
    ExecutionSimulator,
    OrderBook,
    OrderType,
    QueueEvidence,
    SimOrder,
    TimeInForce,
)
from app.backtesting.order_lifecycle import OrderLifecycle, OrderStatus, stop_triggered


def test_depth_execution_consumes_multiple_levels_and_preserves_point_in_time_prices():
    simulator = ExecutionSimulator()
    order = SimOrder("D1", "NSE:SBIN", ExecutionSide.BUY, 120)
    book = OrderBook(
        asks=(DepthLevel(100.0, 50), DepthLevel(100.5, 70), DepthLevel(101.0, 100)),
        bids=(DepthLevel(99.5, 100),),
    )

    result = simulator.execute_depth(order, book, 1_000)

    assert not result.rejected
    assert result.remaining_quantity == 0
    assert [(f.quantity, f.price) for f in result.fills] == [(50, 100.0), (70, 100.5)]
    assert all(f.filled_at_ns == 1_000 for f in result.fills)


def test_queue_ahead_requires_observed_evidence_before_fill():
    simulator = ExecutionSimulator()
    order = SimOrder(
        "Q1", "NSE:SBIN", ExecutionSide.BUY, 40,
        order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=60,
    )
    book = OrderBook(asks=(DepthLevel(100.0, 100),))

    blocked = simulator.execute_depth(order, book, 2_000)
    assert blocked.fills == ()
    assert blocked.rejected
    assert blocked.reason == "queue ahead not depleted"

    evidence = QueueEvidence(100.0, executed_quantity=60)
    advanced = simulator.advance_queue_ahead(order.queue_ahead_quantity, evidence)
    assert advanced == 0

    executable = simulator.execute_depth(
        SimOrder(
            "Q1", "NSE:SBIN", ExecutionSide.BUY, 40,
            order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=advanced,
        ),
        book,
        2_001,
    )
    assert executable.remaining_quantity == 0
    assert executable.fills[0].quantity == 40


def test_ioc_partial_and_fok_insufficient_depth_have_different_terminal_semantics():
    simulator = ExecutionSimulator()
    book = OrderBook(asks=(DepthLevel(100.0, 30),))

    ioc = SimOrder("I1", "NSE:SBIN", ExecutionSide.BUY, 50, time_in_force=TimeInForce.IOC)
    ioc_result = simulator.execute_depth(ioc, book, 3_000)
    assert [(f.quantity, f.price) for f in ioc_result.fills] == [(30, 100.0)]
    assert ioc_result.remaining_quantity == 20

    fok = SimOrder("F1", "NSE:SBIN", ExecutionSide.BUY, 50, time_in_force=TimeInForce.FOK)
    fok_result = simulator.execute_depth(fok, book, 3_000)
    assert fok_result.fills == ()
    assert fok_result.rejected
    assert fok_result.remaining_quantity == 50


def test_stop_trigger_is_observation_driven_and_latency_is_applied_to_fill_timestamp():
    order = SimOrder(
        "S1", "NSE:NIFTY", ExecutionSide.BUY, 1,
        order_type=OrderType.STOP, stop_price=100.0,
    )
    assert not stop_triggered(order, 99.99)
    assert stop_triggered(order, 100.0)

    simulator = ExecutionSimulator(
        ExecutionConfig(slippage_bps=10.0, latency_ns=250, fee_per_unit=2.0)
    )
    fill = simulator.execute(order, 101.0, 4_000)
    assert fill.filled_at_ns == 4_250
    assert round(fill.price, 6) == 101.101
    assert fill.fee == 2.0


def test_lifecycle_export_restore_keeps_fill_and_terminal_state():
    order = SimOrder("R1", "NSE:SBIN", ExecutionSide.BUY, 10, submitted_at_ns=5_000)
    lifecycle = OrderLifecycle(order)
    lifecycle.accept(5_000)
    lifecycle.apply_fill(lifecycle.to_fill(100.0, 5_100, quantity=4))

    restored = OrderLifecycle.restore_state(lifecycle.export_state())
    assert restored.state.status == OrderStatus.PARTIALLY_FILLED
    assert restored.state.filled_quantity == 4
    assert restored.state.remaining_quantity == 6
    assert len(restored.state.events) == len(lifecycle.state.events)

    restored.cancel(5_200, "end of replay")
    assert restored.state.status == OrderStatus.CANCELLED
