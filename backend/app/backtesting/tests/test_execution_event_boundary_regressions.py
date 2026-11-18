import pytest

from app.backtesting.events import EventType, MarketEvent
from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.execution import DepthLevel, OrderBook


def test_event_price_does_not_treat_boolean_as_market_price():
    event = MarketEvent(1, "X", EventType.TICK, {"price": True})
    assert EventBacktestEngine._event_price(event) is None


def test_event_price_ignores_boolean_bid_ask_values():
    event = MarketEvent(1, "X", EventType.TICK, {"bid": True, "ask": 101.0})
    assert EventBacktestEngine._event_price(event) is None


def test_event_depth_boundary_does_not_coerce_string_price():
    with pytest.raises(ValueError, match="invalid DEPTH bids\[0\]"):
        EventBacktestEngine._book_from_event(
            MarketEvent(1, "X", EventType.DEPTH, {"bids": [("100", 10)]})
        )


def test_order_book_rejects_duplicate_bid_price_levels():
    with pytest.raises(ValueError, match="duplicate price levels"):
        OrderBook(bids=(DepthLevel(100.0, 10), DepthLevel(100.0, 5)))


def test_order_book_rejects_duplicate_ask_price_levels():
    with pytest.raises(ValueError, match="duplicate price levels"):
        OrderBook(asks=(DepthLevel(101.0, 10), DepthLevel(101.0, 5)))
