"""Bounded stale-while-refresh cache for read-only dashboard snapshots.

The cache is process-local by design: each ASGI worker refreshes its own snapshot,
without introducing broker sessions, shared-runner ownership, or cross-process locks.
"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable


class BoundedSnapshotCache:
    """Single-flight snapshot cache with bounded stale serving and background refresh."""

    def __init__(self, *, ttl_seconds: float, max_stale_seconds: float, wait_seconds: float = 3.0):
        if ttl_seconds <= 0 or max_stale_seconds < ttl_seconds or wait_seconds <= 0:
            raise ValueError("invalid snapshot cache bounds")
        self.ttl_seconds = float(ttl_seconds)
        self.max_stale_seconds = float(max_stale_seconds)
        self.wait_seconds = float(wait_seconds)
        self._lock = Lock()
        self._refresh_done = Event()
        self._refresh_done.set()
        self._value: Any = None
        self._refreshed_at: float | None = None
        self._refreshing = False
        self._refresh_count = 0
        self._refresh_errors = 0

    def _metadata(self, *, now: float, stale: bool) -> dict[str, Any]:
        age = max(0.0, now - (self._refreshed_at or now))
        return {
            "age_seconds": round(age, 3),
            "stale": stale,
            "refreshing": self._refreshing,
            "refresh_count": self._refresh_count,
            "refresh_errors": self._refresh_errors,
        }

    def _copy_with_metadata(self, *, stale: bool) -> Any:
        now = monotonic()
        value = deepcopy(self._value)
        if isinstance(value, dict):
            value["_snapshot_cache"] = self._metadata(now=now, stale=stale)
        return value

    def _refresh(self, factory: Callable[[], Any]) -> Any:
        try:
            value = factory()
            with self._lock:
                self._value = deepcopy(value)
                self._refreshed_at = monotonic()
                self._refresh_count += 1
            return value
        except Exception:
            with self._lock:
                self._refresh_errors += 1
            raise
        finally:
            with self._lock:
                self._refreshing = False
                self._refresh_done.set()

    def _background_refresh(self, factory: Callable[[], Any]) -> None:
        try:
            self._refresh(factory)
        except Exception:
            # Preserve the last known snapshot; the next request retries after
            # the stale window expires. Never let a daemon exception kill a worker.
            pass

    def clear(self) -> None:
        """Clear a quiescent cache (primarily for deterministic test isolation)."""
        with self._lock:
            if self._refreshing:
                raise RuntimeError("cannot clear a snapshot cache during refresh")
            self._value = None
            self._refreshed_at = None
            self._refresh_count = 0
            self._refresh_errors = 0
            self._refresh_done.set()

    def get(self, factory: Callable[[], Any]) -> Any:
        now = monotonic()
        with self._lock:
            age = None if self._refreshed_at is None else max(0.0, now - self._refreshed_at)
            if age is not None and age <= self.ttl_seconds:
                return self._copy_with_metadata(stale=False)
            if age is not None and age <= self.max_stale_seconds:
                if not self._refreshing:
                    self._refreshing = True
                    self._refresh_done.clear()
                    Thread(target=self._background_refresh, args=(factory,),
                           name="dashboard-snapshot-refresh", daemon=True).start()
                return self._copy_with_metadata(stale=True)
            if self._refreshing:
                wait_for_refresh = True
            else:
                self._refreshing = True
                self._refresh_done.clear()
                wait_for_refresh = False

        if wait_for_refresh:
            self._refresh_done.wait(self.wait_seconds)
            with self._lock:
                age = None if self._refreshed_at is None else max(0.0, monotonic() - self._refreshed_at)
                if self._value is not None and age is not None and age <= self.max_stale_seconds:
                    return self._copy_with_metadata(stale=True)
                raise TimeoutError("dashboard snapshot refresh did not finish within its bound")

        try:
            self._refresh(factory)
        except Exception:
            with self._lock:
                age = None if self._refreshed_at is None else max(0.0, monotonic() - self._refreshed_at)
                if self._value is not None and age is not None and age <= self.max_stale_seconds:
                    return self._copy_with_metadata(stale=True)
            raise
        with self._lock:
            return self._copy_with_metadata(stale=False)


def cached_snapshot(*, ttl_seconds: float, max_stale_seconds: float):
    """Cache a no-argument read-only endpoint with one refresh at a time."""
    cache = BoundedSnapshotCache(
        ttl_seconds=ttl_seconds,
        max_stale_seconds=max_stale_seconds,
    )

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            return cache.get(lambda: function(*args, **kwargs))
        wrapped.snapshot_cache = cache
        return wrapped

    return decorate
