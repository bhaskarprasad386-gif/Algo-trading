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
    try:
        OrderBook(asks=(DepthLevel(101.0, 1), DepthLevel(100.0, 1)))
    except ValueError as exc:
        assert "best-to-worst" in str(exc)
    else:
        raise AssertionError("expected invalid ask ordering to fail")
