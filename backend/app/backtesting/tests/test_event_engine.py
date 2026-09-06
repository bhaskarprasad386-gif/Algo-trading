from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventReplayConfig, EventType, MarketEvent


def test_replays_millisecond_events_without_fabrication():
    events = [
        MarketEvent(100, "NIFTY", EventType.QUOTE, {"bid": 1}),
        MarketEvent(101, "NIFTY", EventType.TRADE, {"price": 2}),
    ]
    received = []
    result = EventBacktestEngine(EventReplayConfig(timestamp_unit="ms")).run(
        events, lambda event, state: received.append(event.timestamp_ns)
    )
    assert received == [100_000_000, 101_000_000]
    assert result.events_seen == 2
    assert result.events_dispatched == 2


def test_supports_microsecond_source_timestamps():
    events = [
        MarketEvent(1_000_000, "OPT", EventType.QUOTE, {}, sequence=1),
        MarketEvent(1_000_001, "OPT", EventType.TRADE, {}, sequence=2),
    ]
    received = []
    result = EventBacktestEngine(EventReplayConfig(timestamp_unit="us")).run(
        events, lambda event, state: received.append(event.timestamp_ns)
    )
    assert received == [1_000_000_000, 1_000_001_000]
    assert result.first_timestamp_ns == 1_000_000_000
    assert result.last_timestamp_ns == 1_000_001_000


def test_preserves_multiple_events_at_same_timestamp_by_sequence():
    events = [
        MarketEvent(10, "FUT", EventType.QUOTE, {}, sequence=2),
        MarketEvent(10, "FUT", EventType.TRADE, {}, sequence=3),
    ]
    received = []
    EventBacktestEngine().run(events, lambda event, state: received.append(event.sequence))
    assert received == [2, 3]


def test_can_filter_depth_events():
    events = [
        MarketEvent(1, "OPT", EventType.TRADE, {}),
        MarketEvent(2, "OPT", EventType.DEPTH, {"bid_qty": 10}),
    ]
    received = []
    result = EventBacktestEngine(
        EventReplayConfig(include_event_types=frozenset({EventType.DEPTH}))
    ).run(events, lambda event, state: received.append(event.event_type))
    assert received == [EventType.DEPTH]
    assert result.events_seen == 2
    assert result.events_dispatched == 1


def test_rejects_out_of_order_source_events():
    events = [
        MarketEvent(2, "NIFTY", EventType.QUOTE, {}),
        MarketEvent(1, "NIFTY", EventType.QUOTE, {}),
    ]
    try:
        EventBacktestEngine().run(events, lambda event, state: None)
    except ValueError as exc:
        assert "ordered" in str(exc)
    else:
        raise AssertionError("expected out-of-order events to fail")
