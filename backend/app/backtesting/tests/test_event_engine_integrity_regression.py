import pytest

from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent


def test_current_marks_keep_last_valid_book_after_non_price_event():
    engine = EventBacktestEngine()
    engine._update_market_state(MarketEvent(
        100, "X", EventType.DEPTH,
        {"bids": [(99, 10)], "asks": [(101, 12)]},
    ))
    engine._update_market_state(MarketEvent(101, "X", EventType.CUSTOM, {}))
    assert engine._current_marks()["X"] == 100.0


def test_depth_rejects_malformed_level_instead_of_silently_dropping_it():
    engine = EventBacktestEngine()
    with pytest.raises(ValueError, match=r"invalid DEPTH bids\[0\]"):
        engine._update_market_state(MarketEvent(
            100, "X", EventType.DEPTH,
            {"bids": [{"price": 99}], "asks": [(101, 12)]},
        ))


def test_queue_evidence_rejects_malformed_entry_instead_of_ignoring_it():
    engine = EventBacktestEngine()
    with pytest.raises(ValueError, match=r"invalid queue_evidence\[0\]"):
        engine._update_market_state(MarketEvent(
            100, "X", EventType.DEPTH,
            {
                "bids": [(99, 10)],
                "asks": [(101, 12)],
                "queue_evidence": [{"executed_quantity": 1}],
            },
        ))
