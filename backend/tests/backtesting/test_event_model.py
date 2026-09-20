from app.backtesting.event_model import EventIdentity, event_identity, event_order_key
from app.backtesting.historical_catalog import HistoricalRecord


def test_event_identity_is_stable_and_complete():
    record = HistoricalRecord("src", "AAA", "tick", 1_000, {"price": 10}, 7)
    assert event_identity(record) == EventIdentity(1_000, "src", "AAA", "tick", 7)


def test_event_order_is_total_for_same_timestamp_without_cadence_assumptions():
    records = [
        HistoricalRecord("z", "AAA", "quote", 100, {}),
        HistoricalRecord("a", "ZZZ", "tick", 100, {}),
        HistoricalRecord("a", "AAA", "tick", 100, {}),
    ]
    ordered = sorted(records, key=event_order_key)
    assert [(r.source, r.instrument, r.timeframe) for r in ordered] == [
        ("a", "AAA", "tick"), ("a", "ZZZ", "tick"), ("z", "AAA", "quote")
    ]


def test_event_order_preserves_sequence_within_one_stream():
    records = [
        HistoricalRecord("src", "AAA", "tick", 100, {}, 2),
        HistoricalRecord("src", "AAA", "tick", 100, {}, 1),
        HistoricalRecord("src", "AAA", "tick", 100, {}, None),
    ]
    ordered = sorted(records, key=event_order_key)
    assert [r.sequence for r in ordered] == [None, 1, 2]
