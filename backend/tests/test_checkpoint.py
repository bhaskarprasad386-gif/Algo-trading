import sqlite3

from app.backtesting.checkpoint import CheckpointStore, ReplayCheckpoint


def test_checkpoint_round_trip_and_resume_state():
    store = CheckpointStore(sqlite3.connect(":memory:"))
    checkpoint = ReplayCheckpoint("run-1", 2_000_000, 7, 1000, 125.5, {"position": 2})
    store.save(checkpoint)
    assert store.load("run-1") == checkpoint


def test_checkpoint_update_is_idempotent():
    store = CheckpointStore(sqlite3.connect(":memory:"))
    store.save(ReplayCheckpoint("run-1", 100, 1, 10, 5.0, {"position": 1}))
    store.save(ReplayCheckpoint("run-1", 200, 2, 20, 8.0, {"position": 0}))
    loaded = store.load("run-1")
    assert loaded is not None
    assert loaded.timestamp_ns == 200
    assert loaded.processed_events == 20
    assert loaded.realized_pnl == 8.0


def test_checkpoint_rejects_stale_cursor_and_preserves_latest():
    store = CheckpointStore(sqlite3.connect(":memory:"))
    latest = ReplayCheckpoint("run-stale", 200, 2, 20, 8.0, {"position": 0})
    store.save(latest)

    import pytest
    with pytest.raises(ValueError, match="cannot move backwards"):
        store.save(ReplayCheckpoint("run-stale", 100, 1, 10, 5.0, {"position": 1}))

    assert store.load("run-stale") == latest


def test_checkpoint_rejects_conflicting_same_cursor_and_accepts_exact_retry():
    store = CheckpointStore(sqlite3.connect(":memory:"))
    checkpoint = ReplayCheckpoint("run-conflict", 200, 2, 20, 8.0, {"position": 0})
    store.save(checkpoint)

    store.save(checkpoint)
    assert store.load("run-conflict") == checkpoint

    import pytest
    with pytest.raises(ValueError, match="conflicting checkpoint"):
        store.save(ReplayCheckpoint("run-conflict", 201, 2, 20, 9.0, {"position": 1}))

    assert store.load("run-conflict") == checkpoint
