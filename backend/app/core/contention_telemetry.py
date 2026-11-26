"""Low-overhead, bounded runtime contention telemetry.

No SQL text, credentials, connection objects, or unbounded request labels are retained.
"""
from __future__ import annotations

from collections import Counter
from contextvars import ContextVar
import threading
import time
from typing import Any

_database_owner: ContextVar[str] = ContextVar("database_owner", default="background")


def set_database_owner(owner: str):
    """Set a short, caller-supplied owner label for checkouts in this context."""
    return _database_owner.set((str(owner or "unknown"))[:96])


def reset_database_owner(token) -> None:
    _database_owner.reset(token)


class PoolCheckoutTelemetry:
    """Track active checkout owners and completed hold-time buckets."""

    BUCKETS = (0.1, 0.5, 1.0, 5.0, 30.0)

    def __init__(self, *, max_owners: int = 24) -> None:
        self._lock = threading.Lock()
        self._active: dict[int, tuple[float, str, str]] = {}
        self._max_owners = max(1, int(max_owners))
        self._owner_totals: Counter[str] = Counter()
        self._hold_buckets: Counter[str] = Counter()
        self._completed = 0
        self._total_hold_seconds = 0.0
        self._max_hold_seconds = 0.0
        self._invalidations = 0
        self._detaches = 0

    def checkout(self, connection_record: Any, *, owner: str | None = None) -> None:
        if connection_record is None:
            return
        label = (owner or _database_owner.get() or "unknown")[:96]
        key = id(connection_record)
        with self._lock:
            if label not in self._owner_totals and len(self._owner_totals) >= self._max_owners:
                label = "other"
            self._active[key] = (time.monotonic(), threading.current_thread().name[:64], label)

    def checkin(self, connection_record: Any, *, event_name: str = "checkin") -> None:
        if connection_record is None:
            return
        with self._lock:
            active = self._active.pop(id(connection_record), None)
            if event_name == "invalidate":
                self._invalidations += 1
            elif event_name == "detach":
                self._detaches += 1
            if active is None:
                return
            duration = max(0.0, time.monotonic() - active[0])
            self._completed += 1
            self._total_hold_seconds += duration
            self._max_hold_seconds = max(self._max_hold_seconds, duration)
            self._owner_totals[active[2]] += 1
            bucket = next((f"le_{bound:g}s" for bound in self.BUCKETS if duration <= bound), "gt_30s")
            self._hold_buckets[bucket] += 1

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            active = tuple(self._active.values())
            owners = Counter(item[2] for item in active)
            oldest = max(active, key=lambda item: now - item[0], default=None)
            long_count = sum(1 for item in active if now - item[0] >= 30.0)
            return {
                "completed_checkouts": self._completed,
                "active_checkouts": len(active),
                "long_checkouts_30s": long_count,
                "active_by_owner": dict(owners),
                "completed_by_owner": dict(self._owner_totals),
                "hold_duration_buckets": dict(self._hold_buckets),
                "average_hold_seconds": round(self._total_hold_seconds / self._completed, 4) if self._completed else None,
                "max_hold_seconds": round(self._max_hold_seconds, 4) if self._completed else None,
                "oldest_active_hold_seconds": round(now - oldest[0], 4) if oldest else None,
                "oldest_active_thread": oldest[1] if oldest else None,
                "oldest_active_owner": oldest[2] if oldest else None,
                "invalidations": self._invalidations,
                "detaches": self._detaches,
            }


class InstrumentedRLock:
    """RLock-compatible wrapper recording bounded acquisition wait/hold metrics."""

    BUCKETS = (0.001, 0.01, 0.05, 0.1, 0.5)

    def __init__(self, name: str) -> None:
        self.name = str(name)[:64]
        self._lock = threading.RLock()
        self._metrics_lock = threading.Lock()
        self._local = threading.local()
        self._acquisitions = 0
        self._wait_total = 0.0
        self._wait_max = 0.0
        self._wait_buckets: Counter[str] = Counter()
        self._hold_total = 0.0
        self._hold_count = 0
        self._hold_max = 0.0
        self._hold_buckets: Counter[str] = Counter()

    @classmethod
    def _bucket(cls, value: float) -> str:
        return next((f"le_{bound:g}s" for bound in cls.BUCKETS if value <= bound), "gt_0.5s")

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        started = time.monotonic()
        acquired = self._lock.acquire(blocking, timeout)
        waited = max(0.0, time.monotonic() - started)
        if acquired:
            depth = getattr(self._local, "depth", 0)
            self._local.depth = depth + 1
            if depth == 0:
                self._local.hold_started = time.monotonic()
            with self._metrics_lock:
                self._acquisitions += 1
                self._wait_total += waited
                self._wait_max = max(self._wait_max, waited)
                self._wait_buckets[self._bucket(waited)] += 1
        return acquired

    def release(self) -> None:
        depth = getattr(self._local, "depth", 0)
        if depth <= 0:
            raise RuntimeError("cannot release an un-acquired lock")
        if depth == 1:
            held = max(0.0, time.monotonic() - self._local.hold_started)
            with self._metrics_lock:
                self._hold_total += held
                self._hold_count += 1
                self._hold_max = max(self._hold_max, held)
                self._hold_buckets[self._bucket(held)] += 1
            del self._local.hold_started
        self._local.depth = depth - 1
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False

    def snapshot(self) -> dict[str, Any]:
        with self._metrics_lock:
            count = self._acquisitions
            return {
                "name": self.name,
                "acquisitions": count,
                "wait_duration_buckets": dict(self._wait_buckets),
                "average_wait_seconds": round(self._wait_total / count, 6) if count else None,
                "max_wait_seconds": round(self._wait_max, 6) if count else None,
                "hold_duration_buckets": dict(self._hold_buckets),
                "average_hold_seconds": round(self._hold_total / self._hold_count, 6) if self._hold_count else None,
                "max_hold_seconds": round(self._hold_max, 6) if self._hold_count else None,
            }
