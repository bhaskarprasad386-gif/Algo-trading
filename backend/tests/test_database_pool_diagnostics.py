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

    assert snapshot == {
        "pool_class": "_QueuePool",
        "size": 5,
        "checkedin": 2,
        "checkedout": 13,
        "overflow": 8,
    }


def test_database_pool_snapshot_is_safe_for_pools_without_queuepool_counters(monkeypatch):
    monkeypatch.setattr(database.engine, "pool", _StaticPool())

    snapshot = database.database_pool_snapshot()

    assert snapshot == {
        "pool_class": "_StaticPool",
        "size": None,
        "checkedin": None,
        "checkedout": None,
        "overflow": None,
    }
