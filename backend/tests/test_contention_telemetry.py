import threading
import time

from app.core.contention_telemetry import InstrumentedRLock, PoolCheckoutTelemetry


def test_pool_checkout_telemetry_tracks_owner_hold_and_wait_buckets():
    telemetry = PoolCheckoutTelemetry(max_owners=2)
    record = object()

    telemetry.checkout(record, owner="http:GET:/api/v1/orders")
    active = telemetry.snapshot()
    assert active["active_checkouts"] == 1
    assert active["active_by_owner"] == {"http:GET:/api/v1/orders": 1}
    assert active["oldest_active_owner"] == "http:GET:/api/v1/orders"

    time.sleep(0.002)
    telemetry.checkin(record)
    telemetry.record_wait(0.02)
    telemetry.record_wait(6.0, timed_out=True)

    result = telemetry.snapshot()
    assert result["completed_checkouts"] == 1
    assert result["completed_by_owner"] == {"http:GET:/api/v1/orders": 1}
    assert result["average_hold_seconds"] is not None
    assert result["checkout_wait_count"] == 2
    assert result["pool_timeout_count"] == 1
    assert result["checkout_wait_buckets"]["le_0.05s"] == 1
    assert result["checkout_wait_buckets"]["gt_5s"] == 1


def test_pool_checkout_telemetry_bounds_owner_cardinality():
    telemetry = PoolCheckoutTelemetry(max_owners=1)
    telemetry.checkout(object(), owner="first-owner")
    telemetry.checkout(object(), owner="second-owner")
    result = telemetry.snapshot()
    assert set(result["active_by_owner"]) <= {"first-owner", "other"}
    assert len(result["active_by_owner"]) <= 2


def test_instrumented_rlock_records_outer_hold_and_acquisition_wait():
    lock = InstrumentedRLock("test-lock")
    lock.acquire()
    lock.acquire()
    lock.release()
    result = []

    def waiter():
        with lock:
            result.append(True)

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.02)
    lock.release()
    thread.join(timeout=1)

    snapshot = lock.snapshot()
    assert result == [True]
    assert snapshot["acquisitions"] == 3
    assert snapshot["max_wait_seconds"] >= 0.01
    # Reentrant nesting is counted as one outer critical-section hold.
    assert sum(snapshot["hold_duration_buckets"].values()) == 2
    assert snapshot["average_hold_seconds"] is not None
