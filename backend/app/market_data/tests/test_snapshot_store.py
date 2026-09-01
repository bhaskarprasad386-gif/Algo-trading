from app.market_data.snapshot_store import SnapshotStore


def test_snapshot_store_keeps_latest_value():
    store = SnapshotStore[int]()
    assert store.get() is None
    store = store.put(42)
    assert store.get() == 42


def test_snapshot_store_replaces_previous_snapshot():
    store = SnapshotStore[str]().put("old")
    store = store.put("new")
    assert store.get() == "new"
