from types import SimpleNamespace

from app.execution.broker_event_sequence_review import review_callback_sequence


def _event(row_id, event_id, *, order="order-1", fill="trade", occurred=100, cumulative=10):
    return SimpleNamespace(
        id=row_id, event_id=event_id, broker_order_id=order, fill_id=fill,
        occurred_at_ns=occurred, cumulative_filled_quantity=cumulative,
    )


def test_callback_sequence_clear_for_monotonic_events():
    result = review_callback_sequence([
        _event(1, "evt-1", fill="trade-1", occurred=100, cumulative=10),
        _event(2, "evt-2", fill="trade-2", occurred=200, cumulative=20),
    ])
    assert result["status"] == "CLEAR"
    assert result["issue_count"] == 0
    assert result["fills_mutated"] is False


def test_detects_late_arrival_of_older_callback_and_cumulative_regression():
    result = review_callback_sequence([
        _event(1, "evt-new", fill="trade-new", occurred=200, cumulative=20),
        _event(2, "evt-old", fill="trade-old", occurred=100, cumulative=10),
    ])
    assert result["status"] == "REQUIRES_REVIEW"
    assert result["reason_counts"]["CALLBACK_ARRIVED_OUT_OF_ORDER"] == 1
    assert result["reason_counts"]["CUMULATIVE_SEQUENCE_NOT_INCREASING"] == 1


def test_detects_duplicate_fill_identity_under_distinct_callback_ids():
    result = review_callback_sequence([
        _event(1, "evt-1", fill="same-trade", occurred=100, cumulative=10),
        _event(2, "evt-2", fill="same-trade", occurred=200, cumulative=20),
    ])
    assert result["reason_counts"]["DUPLICATE_FILL_IDENTITY"] == 1


def test_sequence_diagnostics_are_isolated_per_broker_order():
    result = review_callback_sequence([
        _event(1, "evt-a1", order="order-a", fill="a1", occurred=100, cumulative=10),
        _event(2, "evt-b1", order="order-b", fill="b1", occurred=100, cumulative=10),
        _event(3, "evt-a2", order="order-a", fill="a2", occurred=200, cumulative=20),
        _event(4, "evt-b2", order="order-b", fill="b2", occurred=200, cumulative=20),
    ])
    assert result["status"] == "CLEAR"
    assert result["examined_order_count"] == 2


def test_events_without_trusted_fill_identity_or_timestamp_are_not_ordered():
    result = review_callback_sequence([
        _event(1, "snapshot", fill=None, occurred=0, cumulative=0),
        _event(2, "unknown-time", fill="trade", occurred=0, cumulative=10),
    ])
    assert result["status"] == "CLEAR"
    assert result["examined_event_count"] == 0
