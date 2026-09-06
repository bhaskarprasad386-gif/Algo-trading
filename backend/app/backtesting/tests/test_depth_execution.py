import pytest

from app.backtesting.execution import (
    DepthLevel,
    ExecutionConfig,
    ExecutionSide,
    ExecutionSimulator,
    OrderBook,
    OrderType,
    SimOrder,
)


def test_market_buy_consumes_multiple_ask_levels_without_inventing_liquidity():
    book = OrderBook(
        asks=(DepthLevel(100.0, 3), DepthLevel(100.5, 4), DepthLevel(101.0, 10)),
    )
    order = SimOrder("o1", "NIFTY", ExecutionSide.BUY, 6, submitted_at_ns=1)
    result = ExecutionSimulator().execute_depth(order, book, 2)

    assert [f.quantity for f in result.fills] == [3, 3]
    assert [f.price for f in result.fills] == [100.0, 100.5]
    assert result.remaining_quantity == 0
    assert not result.rejected


def test_market_order_reports_partial_fill_when_displayed_depth_is_insufficient():
    book = OrderBook(asks=(DepthLevel(100.0, 2),))
    order = SimOrder("o2", "OPT", ExecutionSide.BUY, 5, submitted_at_ns=1)
    result = ExecutionSimulator().execute_depth(order, book, 2)

    assert result.fills[0].quantity == 2
    assert result.remaining_quantity == 3
    assert result.reason == "partial fill"


def test_limit_order_does_not_cross_unavailable_levels():
    book = OrderBook(asks=(DepthLevel(100.0, 2), DepthLevel(101.0, 5)))
    order = SimOrder("o3", "FUT", ExecutionSide.BUY, 4, OrderType.LIMIT, limit_price=100.0, submitted_at_ns=1)
    result = ExecutionSimulator().execute_depth(order, book, 2)

    assert result.fills[0].price == 100.0
    assert result.fills[0].quantity == 2
    assert result.remaining_quantity == 2


def test_non_partial_mode_rejects_when_depth_cannot_fully_fill():
    book = OrderBook(bids=(DepthLevel(99.5, 2),))
    order = SimOrder("o4", "FUT", ExecutionSide.SELL, 3, submitted_at_ns=1)
    result = ExecutionSimulator(ExecutionConfig(allow_partial_fills=False)).execute_depth(order, book, 2)

    assert result.rejected
    assert result.fills == ()
    assert result.remaining_quantity == 3


def test_order_book_requires_best_to_worst_ordering():
    with pytest.raises(ValueError, match="best-to-worst"):
        OrderBook(asks=(DepthLevel(101.0, 1), DepthLevel(100.0, 1)))


def test_queue_ahead_delays_execution_without_inventing_liquidity():
    book = OrderBook(asks=(DepthLevel(100.0, 5), DepthLevel(100.5, 5)))
    order = SimOrder("queue", "NIFTY", ExecutionSide.BUY, 4, submitted_at_ns=1, queue_ahead_quantity=3)
    result = ExecutionSimulator().execute_depth(order, book, 2)

    assert [f.quantity for f in result.fills] == [2, 2]
    assert [f.price for f in result.fills] == [100.0, 100.5]
    assert result.remaining_quantity == 0


def test_queue_ahead_can_leave_order_unfilled_when_observed_depth_is_consumed():
    book = OrderBook(asks=(DepthLevel(100.0, 3),))
    order = SimOrder("blocked", "NIFTY", ExecutionSide.BUY, 2, submitted_at_ns=1, queue_ahead_quantity=3)
    result = ExecutionSimulator().execute_depth(order, book, 2)

    assert result.rejected
    assert result.fills == ()
    assert result.remaining_quantity == 2
    assert result.reason == "queue ahead not depleted"


def test_fok_is_atomic_when_displayed_depth_is_insufficient():
    book = OrderBook(asks=(DepthLevel(100.0, 2), DepthLevel(100.5, 1)))
    order = SimOrder("fok", "NIFTY", ExecutionSide.BUY, 4, submitted_at_ns=1)
    result = ExecutionSimulator(ExecutionConfig(allow_partial_fills=False)).execute_depth(order, book, 2)

    assert result.rejected
    assert result.fills == ()
    assert result.remaining_quantity == 4
    assert result.reason == "insufficient displayed depth"


def test_fully_executable_order_still_fills_with_non_partial_mode():
    book = OrderBook(bids=(DepthLevel(99.0, 2), DepthLevel(98.5, 3)))
    order = SimOrder("fok2", "FUT", ExecutionSide.SELL, 4, submitted_at_ns=1)
    result = ExecutionSimulator(ExecutionConfig(allow_partial_fills=False)).execute_depth(order, book, 2)

    assert not result.rejected
    assert result.remaining_quantity == 0
    assert [f.quantity for f in result.fills] == [2, 2]
