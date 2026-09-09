from backend.app.backtesting.execution import (
    DepthLevel,
    ExecutionSimulator,
    OrderBook,
    QueueEvidence,
    SimOrder,
    ExecutionSide,
)


def _buy_order(**kwargs):
    return SimOrder(
        order_id="Q1",
        instrument="NSE:TEST",
        side=ExecutionSide.BUY,
        quantity=10,
        submitted_at_ns=100,
        **kwargs,
    )


def test_resting_queue_requires_explicit_evidence_before_fill():
    sim = ExecutionSimulator()
    order = _buy_order(queue_ahead_quantity=5)
    book = OrderBook(asks=(DepthLevel(100.0, 10),))

    blocked = sim.execute_depth(order, book, 200)
    assert blocked.rejected
    assert blocked.fills == ()
    assert blocked.remaining_quantity == 10

    filled = sim.execute_depth(
        order,
        book,
        300,
        queue_evidence=(QueueEvidence(100.0, executed_quantity=5),),
    )
    assert not filled.rejected
    assert filled.remaining_quantity == 0
    assert [(f.quantity, f.price) for f in filled.fills] == [(10, 100.0)]


def test_dynamic_depth_does_not_reuse_unchanged_displayed_quantity():
    sim = ExecutionSimulator()
    order = _buy_order(quantity=6)
    first = OrderBook(asks=(DepthLevel(100.0, 4),))
    unchanged = OrderBook(asks=(DepthLevel(100.0, 4),))
    replenished = OrderBook(asks=(DepthLevel(100.0, 6),))

    result = sim.execute_depth_updates(
        order,
        (
            (200, first, ()),
            (300, unchanged, ()),
            (400, replenished, ()),
        ),
    )

    assert not result.rejected
    assert result.remaining_quantity == 0
    assert sum(f.quantity for f in result.fills) == 6
    assert [(f.filled_at_ns, f.quantity) for f in result.fills] == [
        (200, 4),
        (400, 2),
    ]


def test_queue_advancement_accepts_cancellation_ahead_without_fabricating_depth():
    sim = ExecutionSimulator()
    order = _buy_order(queue_ahead_quantity=3)
    book = OrderBook(asks=(DepthLevel(100.0, 2),))

    result = sim.execute_depth(
        order,
        book,
        200,
        queue_evidence=(QueueEvidence(100.0, cancelled_quantity_ahead=3),),
    )

    assert not result.rejected
    assert result.remaining_quantity == 8
    assert sum(f.quantity for f in result.fills) == 2
    assert all(f.price == 100.0 for f in result.fills)
