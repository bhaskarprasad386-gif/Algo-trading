import pytest

from app.backtesting.event_replay import DeterministicEventReplay, ReplayEvent


def test_validate_ordered_does_not_require_buffering():
    source = (ReplayEvent(i, 0, {"i": i}) for i in range(3))
    assert [e.timestamp_ns for e in DeterministicEventReplay.validate_ordered(source)] == [0, 1, 2]


def test_validate_ordered_rejects_out_of_order_stream():
    with pytest.raises(ValueError, match="not in deterministic"):
        list(DeterministicEventReplay.validate_ordered([
            ReplayEvent(2, 0, {}), ReplayEvent(1, 0, {})
        ]))


def test_validate_ordered_rejects_duplicate_identity():
    with pytest.raises(ValueError, match="duplicate"):
        list(DeterministicEventReplay.validate_ordered([
            ReplayEvent(1, 0, {}), ReplayEvent(1, 0, {})
        ]))
