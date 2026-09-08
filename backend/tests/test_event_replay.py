import pytest

from app.backtesting.event_replay import DeterministicEventReplay, ReplayEvent


def test_events_are_ordered_by_timestamp_then_sequence():
    events = [
        ReplayEvent(2_000_000, 1, {"id": "b"}),
        ReplayEvent(1_000_000, 5, {"id": "c"}),
        ReplayEvent(1_000_000, 2, {"id": "a"}),
    ]
    ordered = DeterministicEventReplay.validate(events)
    assert [(e.timestamp_ns, e.sequence) for e in ordered] == [
        (1_000_000, 2), (1_000_000, 5), (2_000_000, 1)
    ]


def test_duplicate_event_identity_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        DeterministicEventReplay.validate([
            ReplayEvent(1, 0, {}), ReplayEvent(1, 0, {})
        ])
