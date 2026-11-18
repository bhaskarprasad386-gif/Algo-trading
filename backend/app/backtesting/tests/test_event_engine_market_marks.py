from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventType, MarketEvent


def test_depth_event_provides_midpoint_market_mark_when_no_top_level_price():
    engine = EventBacktestEngine()
    event = MarketEvent(
        1_000,
        "NIFTY",
        EventType.DEPTH,
        {"bids": [[99.0, 10]], "asks": [[101.0, 10]]},
    )

    engine._update_market_state(event)

    assert engine._current_marks() == {"NIFTY": 100.0}


def test_depth_event_with_one_side_still_provides_valid_mark():
    engine = EventBacktestEngine()
    event = MarketEvent(
        1_000,
        "NIFTY",
        EventType.DEPTH,
        {"bids": [[99.0, 10]], "asks": []},
    )

    engine._update_market_state(event)

    assert engine._current_marks() == {"NIFTY": 99.0}
