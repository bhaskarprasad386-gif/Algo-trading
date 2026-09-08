import sqlite3

from app.backtesting.event_dedup import EventDedupStore


def test_duplicate_event_is_rejected_durably():
    store = EventDedupStore(sqlite3.connect(":memory:"))
    assert store.mark_if_new("run-1", 1_000_000, 4) is True
    assert store.mark_if_new("run-1", 1_000_000, 4) is False
    assert store.contains("run-1", 1_000_000, 4) is True


def test_same_timestamp_different_sequence_is_distinct():
    store = EventDedupStore(sqlite3.connect(":memory:"))
    assert store.mark_if_new("run-1", 1_000_000, 1) is True
    assert store.mark_if_new("run-1", 1_000_000, 2) is True
