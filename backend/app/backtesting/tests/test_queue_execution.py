from app.backtesting.execution import (
    DepthLevel,
    ExecutionSimulator,
    ExecutionSide,
    OrderBook,
    OrderType,
    QueueEvidence,
    SimOrder,
)


def test_queue_ahead_requires_explicit_evidence_before_fill():
    simulator = ExecutionSimulator()
    order = SimOrder("queue-1", "NIFTY", ExecutionSide.BUY, 4, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=5)
    blocked = simulator.execute_depth(order, OrderBook(asks=(DepthLevel(100.0, 9),)), 1_000)
    assert blocked.fills == ()
    assert blocked.remaining_quantity == 4
    assert blocked.rejected is True
    assert blocked.reason == "queue ahead not depleted"

    remaining_queue = simulator.advance_queue_ahead(5, QueueEvidence(100.0, executed_quantity=5))
    executable = simulator.execute_depth(
        SimOrder("queue-1", "NIFTY", ExecutionSide.BUY, 4, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=remaining_queue),
        OrderBook(asks=(DepthLevel(100.0, 4),)),
        2_000,
    )
    assert sum(fill.quantity for fill in executable.fills) == 4
    assert executable.remaining_quantity == 0
    assert executable.rejected is False


def test_queue_ahead_is_consumed_across_multiple_price_levels_only_by_evidence():
    simulator = ExecutionSimulator()
    order = SimOrder("queue-2", "NIFTY", ExecutionSide.BUY, 3, order_type=OrderType.LIMIT, limit_price=101.0, queue_ahead_quantity=4)
    blocked = simulator.execute_depth(order, OrderBook(asks=(DepthLevel(100.0, 2), DepthLevel(101.0, 5))), 3_000)
    assert blocked.fills == ()
    assert blocked.remaining_quantity == 3
    assert blocked.rejected is True
    assert blocked.reason == "queue ahead not depleted"

    remaining_queue = simulator.advance_queue_ahead(4, QueueEvidence(101.0, executed_quantity=4))
    result = simulator.execute_depth(
        SimOrder("queue-2", "NIFTY", ExecutionSide.BUY, 3, order_type=OrderType.LIMIT, limit_price=101.0, queue_ahead_quantity=remaining_queue),
        OrderBook(asks=(DepthLevel(100.0, 2), DepthLevel(101.0, 5))),
        3_001,
    )
    assert [(fill.price, fill.quantity) for fill in result.fills] == [(100.0, 2), (101.0, 1)]
    assert result.remaining_quantity == 0
    assert result.rejected is False


def test_queue_ahead_never_creates_synthetic_liquidity():
    simulator = ExecutionSimulator()
    order = SimOrder("queue-3", "NIFTY", ExecutionSide.BUY, 10, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=2)
    result = simulator.execute_depth(order, OrderBook(asks=(DepthLevel(100.0, 3),)), 4_000)
    assert result.fills == ()
    assert result.remaining_quantity == 10
    assert result.rejected is True
    assert result.reason == "queue ahead not depleted"


def test_queue_evidence_at_other_price_does_not_advance_order():
    simulator = ExecutionSimulator()
    queue = simulator.advance_queue_ahead(5, QueueEvidence(101.0, executed_quantity=5))
    assert queue == 0
    order = SimOrder("queue-4", "NIFTY", ExecutionSide.BUY, 2, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=5)
    blocked = simulator.execute_depth(order, OrderBook(asks=(DepthLevel(100.0, 2),)), 5_000)
    assert blocked.fills == ()
    assert blocked.remaining_quantity == 2
