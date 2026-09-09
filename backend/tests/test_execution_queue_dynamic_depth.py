from app.backtesting.execution import (
    DepthLevel,
    ExecutionSide,
    ExecutionSimulator,
    OrderBook,
    OrderType,
    QueueEvidence,
    SimOrder,
    TimeInForce,
)


def _buy_order(*, quantity: int, queue_ahead: int = 0) -> SimOrder:
    return SimOrder(
        order_id="Q1",
        instrument="NSE:TEST",
        side=ExecutionSide.BUY,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        limit_price=101.0,
        submitted_at_ns=100,
        queue_ahead_quantity=queue_ahead,
        time_in_force=TimeInForce.DAY,
    )


def test_queue_ahead_requires_explicit_source_evidence():
    simulator = ExecutionSimulator()
    book = OrderBook(asks=(DepthLevel(100.0, 10),))

    blocked = simulator.execute_depth(_buy_order(quantity=5, queue_ahead=4), book, 200)
    assert blocked.rejected
    assert blocked.fills == ()
    assert blocked.remaining_quantity == 5

    filled = simulator.execute_depth(
        _buy_order(quantity=5, queue_ahead=4),
        book,
        300,
        (QueueEvidence(price=100.0, executed_quantity=4),),
    )
    assert not filled.rejected
    assert [(fill.quantity, fill.price) for fill in filled.fills] == [(5, 100.0)]
    assert filled.remaining_quantity == 0


def test_cancellation_ahead_can_advance_queue_but_depth_disappearance_cannot():
    simulator = ExecutionSimulator()
    order = _buy_order(quantity=3, queue_ahead=5)
    book = OrderBook(asks=(DepthLevel(100.0, 8),))

    blocked = simulator.execute_depth(order, book, 200)
    assert blocked.rejected

    advanced = simulator.execute_depth(
        order,
        OrderBook(asks=(DepthLevel(100.0, 3),)),
        300,
        (QueueEvidence(price=100.0, cancelled_quantity_ahead=5),),
    )
    assert not advanced.rejected
    assert advanced.fills[0].quantity == 3


def test_dynamic_depth_does_not_reuse_unchanged_displayed_quantity():
    simulator = ExecutionSimulator()
    order = _buy_order(quantity=15)
    updates = (
        (200, OrderBook(asks=(DepthLevel(100.0, 10),)), ()),
        (300, OrderBook(asks=(DepthLevel(100.0, 10),)), ()),
        (400, OrderBook(asks=(DepthLevel(100.0, 16),)), ()),
    )

    result = simulator.execute_depth_updates(order, updates)

    assert not result.rejected
    assert result.remaining_quantity == 0
    assert [(fill.quantity, fill.price, fill.filled_at_ns) for fill in result.fills] == [
        (10, 100.0, 200),
        (5, 100.0, 400),
    ]
