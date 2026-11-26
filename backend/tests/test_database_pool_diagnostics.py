from app.core import database


class _QueuePool:
    def size(self):
        return 5

    def checkedin(self):
        return 2

    def checkedout(self):
        return 13

    def overflow(self):
        return 8


class _StaticPool:
    def status(self):
        return "StaticPool"


def test_database_pool_snapshot_reports_queue_pool_occupancy(monkeypatch):
    monkeypatch.setattr(database.engine, "pool", _QueuePool())

    snapshot = database.database_pool_snapshot()

    assert snapshot["pool_class"] == "_QueuePool"
    assert snapshot["size"] == 5
    assert snapshot["checkedin"] == 2
    assert snapshot["checkedout"] == 13
    assert snapshot["overflow"] == 8
    assert snapshot["active_checkout_count"] == 0
    assert snapshot["long_checkout_count"] == 0


def test_database_pool_snapshot_is_safe_for_pools_without_queuepool_counters(monkeypatch):
    monkeypatch.setattr(database.engine, "pool", _StaticPool())

    snapshot = database.database_pool_snapshot()

    assert snapshot["pool_class"] == "_StaticPool"
    assert snapshot["size"] is None
    assert snapshot["checkedin"] is None
    assert snapshot["checkedout"] is None
    assert snapshot["overflow"] is None
    assert snapshot["active_checkout_count"] == 0
    assert snapshot["long_checkout_count"] == 0



def test_database_pool_snapshot_reports_active_and_long_checkouts(monkeypatch):
    import time

    monkeypatch.setattr(database.engine, "pool", _QueuePool())
    now = time.monotonic()
    monkeypatch.setattr(
        database,
        "_pool_checkouts",
        {
            1: (now - 45.0, "history-worker"),
            2: (now - 2.0, "api-worker"),
        },
    )

    snapshot = database.database_pool_snapshot()

    assert snapshot["active_checkout_count"] == 2
    assert snapshot["long_checkout_count"] == 1
    assert snapshot["oldest_checkout_seconds"] >= 44.0
    assert snapshot["oldest_checkout_thread"] == "history-worker"
    assert snapshot["long_checkout_threshold_seconds"] == 30.0


def test_pool_checkout_tracking_is_removed_on_checkin_and_invalidation():
    class _ConnectionRecord:
        pass

    record = _ConnectionRecord()
    database._track_pool_checkout(None, record)
    assert id(record) in database._pool_checkouts

    database._clear_pool_checkout(None, record)
    assert id(record) not in database._pool_checkouts

    database._track_pool_checkout(None, record)
    database._clear_pool_checkout(None, record, RuntimeError("invalidated"))
    assert id(record) not in database._pool_checkouts
