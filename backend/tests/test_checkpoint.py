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
