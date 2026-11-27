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
        self._last_refresh_error: Exception | None = None

    def _metadata(self, *, now: float, stale: bool) -> dict[str, Any]:
        refreshed_at = self._refreshed_at
        age = max(0.0, now - refreshed_at) if refreshed_at is not None else 0.0
        return {
            "age_seconds": round(age, 3),
            "stale": stale,
            "refreshing": self._refreshing,
            "refresh_count": self._refresh_count,
            "refresh_errors": self._refresh_errors,
        }

    @staticmethod
    def _copy_with_metadata(value: Any, metadata: dict[str, Any]) -> Any:
        """Copy a captured immutable-by-convention value after releasing _lock."""
        snapshot = deepcopy(value)
        if isinstance(snapshot, dict):
            snapshot["_snapshot_cache"] = metadata
        return snapshot

    def _refresh(self, factory: Callable[[], Any]) -> Any:
        try:
            value = factory()
            with self._lock:
                self._value = deepcopy(value)
                self._refreshed_at = monotonic()
                self._refresh_count += 1
                self._last_refresh_error = None
            return value
        except Exception as exc:
            with self._lock:
                self._refresh_errors += 1
                self._last_refresh_error = exc
            raise
        finally:
            with self._lock:
                self._refreshing = False
                self._refresh_done.set()

    def _start_refresh_locked(self, factory: Callable[[], Any]) -> None:
        """Start exactly one bounded worker; caller holds _lock."""
        self._refreshing = True
        self._refresh_done.clear()
        try:
            worker = Thread(
                target=self._background_refresh,
                args=(factory,),
                name="dashboard-snapshot-refresh",
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            # Thread exhaustion must not leave the cache permanently marked
            # refreshing. Existing stale data remains serviceable.
            self._refreshing = False
            self._refresh_errors += 1
            self._last_refresh_error = exc
            self._refresh_done.set()
            raise

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
            self._last_refresh_error = None
            self._refresh_done.set()

    def get(self, factory: Callable[[], Any]) -> Any:
        now = monotonic()
        with self._lock:
            age = None if self._refreshed_at is None else max(0.0, now - self._refreshed_at)
            if age is not None and age <= self.ttl_seconds:
                value = self._value
                metadata = self._metadata(now=now, stale=False)
                # _value is replaced, never mutated, by _refresh. Capture its
                # reference and metadata under lock, then perform the expensive
                # recursive copy after releasing the lock.
                pass
            else:
                value = None
                metadata = None

            has_usable_stale = (
                self._value is not None
                and age is not None
                and age <= self.max_stale_seconds
            )
            if has_usable_stale:
                if not self._refreshing:
                    try:
                        self._start_refresh_locked(factory)
                    except Exception:
                        # Stale-while-refresh is explicitly best effort.
                        pass
                value = self._value
                metadata = self._metadata(now=now, stale=True)
            else:
                value = None
                metadata = None

            if has_usable_stale:
                # Keep the lock only for cache state and single-flight control.
                pass
            elif not self._refreshing:
                self._start_refresh_locked(factory)

        if value is not None:
            return self._copy_with_metadata(value, metadata)

        # Cold and hard-expired refreshes run off-request in one worker. A
        # slow shard scan/provider call can no longer pin a request thread
        # indefinitely; callers wait only for the configured response budget.
        finished = self._refresh_done.wait(self.wait_seconds)
        with self._lock:
            age = None if self._refreshed_at is None else max(0.0, monotonic() - self._refreshed_at)
            if self._value is not None and age is not None and age <= self.max_stale_seconds:
                value = self._value
                metadata = self._metadata(
                    now=monotonic(), stale=age > self.ttl_seconds
                )
            else:
                value = None
                metadata = None
            if value is None and finished and self._last_refresh_error is not None:
                raise self._last_refresh_error
            if value is None:
                raise TimeoutError("dashboard snapshot refresh did not finish within its bound")
        return self._copy_with_metadata(value, metadata)


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
