from app.backtesting.execution import (
    DepthLevel,
    ExecutionSimulator,
    ExecutionSide,
    OrderBook,
    OrderType,
    SimOrder,
)


def test_queue_ahead_prevents_fill_until_displayed_queue_is_depleted():
    simulator = ExecutionSimulator()
    order = SimOrder("queue-1", "NIFTY", ExecutionSide.BUY, 4, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=5)
    blocked = simulator.execute_depth(order, OrderBook(asks=(DepthLevel(100.0, 5),)), 1_000)
    assert blocked.fills == ()
    assert blocked.remaining_quantity == 4
    assert blocked.rejected is True
    assert blocked.reason == "queue ahead not depleted"

    executable = simulator.execute_depth(order, OrderBook(asks=(DepthLevel(100.0, 9),)), 2_000)
    assert sum(fill.quantity for fill in executable.fills) == 4
    assert executable.remaining_quantity == 0
    assert executable.rejected is False


def test_queue_ahead_is_consumed_across_multiple_price_levels():
    simulator = ExecutionSimulator()
    order = SimOrder("queue-2", "NIFTY", ExecutionSide.BUY, 3, order_type=OrderType.LIMIT, limit_price=101.0, queue_ahead_quantity=4)
    result = simulator.execute_depth(order, OrderBook(asks=(DepthLevel(100.0, 2), DepthLevel(101.0, 5))), 3_000)
    assert [(fill.price, fill.quantity) for fill in result.fills] == [(101.0, 3)]
    assert result.remaining_quantity == 0
    assert result.rejected is False


def test_queue_ahead_never_creates_synthetic_liquidity():
    simulator = ExecutionSimulator()
    order = SimOrder("queue-3", "NIFTY", ExecutionSide.BUY, 10, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=2)
    result = simulator.execute_depth(order, OrderBook(asks=(DepthLevel(100.0, 3),)), 4_000)
    assert sum(fill.quantity for fill in result.fills) == 1
    assert result.remaining_quantity == 9
    assert result.reason == "partial fill"
