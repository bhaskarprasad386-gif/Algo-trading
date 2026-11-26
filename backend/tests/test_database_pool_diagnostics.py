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

    _max_overflow = 10


class _StaticPool:
    def status(self):
        return "StaticPool"


def _reset_telemetry(monkeypatch):
    from app.core.contention_telemetry import PoolCheckoutTelemetry

    telemetry = PoolCheckoutTelemetry(max_owners=24)
    monkeypatch.setattr(database, "_pool_checkout_telemetry", telemetry)
    return telemetry


def test_database_pool_snapshot_reports_queue_pool_occupancy(monkeypatch):
    monkeypatch.setattr(database.engine, "pool", _QueuePool())
    _reset_telemetry(monkeypatch)

    snapshot = database.database_pool_snapshot()

    assert snapshot["pool_class"] == "_QueuePool"
    assert snapshot["size"] == 5
    assert snapshot["checkedin"] == 2
    assert snapshot["checkedout"] == 13
    assert snapshot["overflow"] == 8
    assert snapshot["max_overflow"] == 10
    assert snapshot["capacity"] == 15
    assert snapshot["saturated"] is False
    assert snapshot["active_checkout_count"] == 0
    assert snapshot["long_checkout_count"] == 0
    assert snapshot["checkout_tracking_delta"] == 13
    assert snapshot["untracked_checkout_count"] is None
    assert snapshot["checkout_tracking_status"] == "COUNTER_MISMATCH"


def test_database_pool_snapshot_is_safe_for_pools_without_queuepool_counters(monkeypatch):
    monkeypatch.setattr(database.engine, "pool", _StaticPool())
    _reset_telemetry(monkeypatch)

    snapshot = database.database_pool_snapshot()

    assert snapshot["pool_class"] == "_StaticPool"
    assert snapshot["size"] is None
    assert snapshot["checkedin"] is None
    assert snapshot["checkedout"] is None
    assert snapshot["overflow"] is None
    assert snapshot["max_overflow"] is None
    assert snapshot["capacity"] is None
    assert snapshot["saturated"] is None
    assert snapshot["active_checkout_count"] == 0
    assert snapshot["long_checkout_count"] == 0
    assert snapshot["checkout_tracking_delta"] is None
    assert snapshot["untracked_checkout_count"] is None
    assert snapshot["checkout_tracking_status"] == "UNAVAILABLE"


def test_database_pool_snapshot_reports_active_and_long_checkouts(monkeypatch):
    import time

    monkeypatch.setattr(database.engine, "pool", _QueuePool())
    telemetry = _reset_telemetry(monkeypatch)
    with telemetry._lock:
        telemetry._active[1] = (time.monotonic() - 45.0, "history-worker", "background")
        telemetry._active[2] = (time.monotonic() - 2.0, "api-worker", "http:GET:/api")
    snapshot = database.database_pool_snapshot()

    assert snapshot["active_checkout_count"] == 2
    assert snapshot["long_checkout_count"] == 1
    assert snapshot["oldest_checkout_seconds"] >= 44.0
    assert snapshot["oldest_checkout_thread"] == "history-worker"
    assert snapshot["long_checkout_threshold_seconds"] == 30.0
    assert snapshot["checkout_tracking_delta"] == 11
    assert snapshot["untracked_checkout_count"] is None
    assert snapshot["checkout_tracking_status"] == "COUNTER_MISMATCH"


def test_pool_checkout_tracking_is_removed_on_checkin_and_invalidation(monkeypatch):
    class _ConnectionRecord:
        pass

    _reset_telemetry(monkeypatch)
    record = _ConnectionRecord()
    database._track_pool_checkout(None, record)
    assert database._pool_checkout_telemetry.snapshot()["active_checkouts"] == 1

    database._clear_pool_checkout(None, record)
    assert database._pool_checkout_telemetry.snapshot()["active_checkouts"] == 0

    database._track_pool_checkout(None, record)
    database._invalidate_pool_checkout(None, record, RuntimeError("invalidated"))
    result = database._pool_checkout_telemetry.snapshot()
    assert result["active_checkouts"] == 0
    assert result["invalidations"] == 1


def test_database_pool_snapshot_marks_queue_pool_saturated(monkeypatch):
    class _SaturatedPool(_QueuePool):
        def checkedout(self):
            return 15

    monkeypatch.setattr(database.engine, "pool", _SaturatedPool())
    _reset_telemetry(monkeypatch)
    snapshot = database.database_pool_snapshot()

    assert snapshot["capacity"] == 15
    assert snapshot["checkedout"] == 15
    assert snapshot["saturated"] is True


def test_database_pool_snapshot_does_not_invent_capacity_for_unbounded_queue_pool(monkeypatch):
    class _UnboundedQueuePool(_QueuePool):
        def size(self):
            return 0

        def checkedout(self):
            return 27

    monkeypatch.setattr(database.engine, "pool", _UnboundedQueuePool())
    _reset_telemetry(monkeypatch)
    snapshot = database.database_pool_snapshot()

    assert snapshot["size"] == 0
    assert snapshot["checkedout"] == 27
    assert snapshot["capacity"] is None
    assert snapshot["saturated"] is None


def test_database_pool_snapshot_does_not_invent_capacity_for_unlimited_overflow(monkeypatch):
    class _UnlimitedOverflowPool(_QueuePool):
        _max_overflow = -1

        def checkedout(self):
            return 500

    monkeypatch.setattr(database.engine, "pool", _UnlimitedOverflowPool())
    _reset_telemetry(monkeypatch)
    snapshot = database.database_pool_snapshot()

    assert snapshot["max_overflow"] == -1
    assert snapshot["capacity"] is None
    assert snapshot["saturated"] is None


def test_pool_checkout_tracking_is_removed_when_connection_is_detached(monkeypatch):
    from sqlalchemy import event

    class _ConnectionRecord:
        pass

    _reset_telemetry(monkeypatch)
    record = _ConnectionRecord()
    database._track_pool_checkout(None, record)
    assert database._pool_checkout_telemetry.snapshot()["active_checkouts"] == 1

    assert event.contains(database.engine, "detach", database._detach_pool_checkout)
    database._detach_pool_checkout(None, record)
    result = database._pool_checkout_telemetry.snapshot()
    assert result["active_checkouts"] == 0
    assert result["detaches"] == 1
