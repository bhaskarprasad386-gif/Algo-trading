from app.market_data.sqlite_store import SQLiteSnapshotStore


def test_sqlite_snapshot_round_trip(tmp_path):
    store = SQLiteSnapshotStore(tmp_path / "market.db")
    store.put("nifty", "{\"ltp\":25000}")
    assert store.get("nifty").payload == "{\"ltp\":25000}"


def test_sqlite_snapshot_updates_existing_key(tmp_path):
    store = SQLiteSnapshotStore(tmp_path / "market.db")
    store.put("nifty", "old")
    store.put("nifty", "new")
    assert store.get("nifty").payload == "new"
