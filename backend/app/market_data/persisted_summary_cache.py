"""Bounded cache for historical persistence summaries used by live-health.

Persisted row counts are historical coverage metadata, not live feed health.
They can be refreshed less often than in-memory tick freshness while the
endpoint continues to calculate runtime feed age on every snapshot refresh.
"""
from __future__ import annotations

from copy import deepcopy
from threading import Lock
from time import monotonic
from typing import Callable, Any


class BoundedPersistedSummaryCache:
    """Single-flight, bounded-age cache for slow read-only persisted summaries."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 60.0,
        max_stale_seconds: float = 300.0,
        max_entries: int = 4,
        failure_retry_seconds: float = 15.0,
    ) -> None:
        if (
            ttl_seconds <= 0 or max_stale_seconds < ttl_seconds
            or max_entries < 1 or failure_retry_seconds < 0
        ):
            raise ValueError("invalid persisted summary cache bounds")
        self.ttl_seconds = float(ttl_seconds)
        self.max_stale_seconds = float(max_stale_seconds)
        self.max_entries = int(max_entries)
        self.failure_retry_seconds = float(failure_retry_seconds)
        self._lock = Lock()
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}
        self._failures: dict[str, tuple[float, str]] = {}
        self._refresh_count = 0
        self._refresh_errors = 0
        self._last_error_type: str | None = None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._failures.clear()
            self._refresh_count = 0
            self._refresh_errors = 0
            self._last_error_type = None

    def get(
        self,
        key: str,
        loader: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return a copied summary plus freshness metadata.

        The lock intentionally covers the loader: at most one request per
        process can enumerate/open the shard set at a time. The loader must be
        read-only and bounded by the SQLite busy timeout.
        """
        normalized_key = str(key)
        with self._lock:
            now = monotonic()
            cached = self._entries.get(normalized_key)
            age = None if cached is None else max(0.0, now - cached[0])
            if cached is not None and age is not None and age <= self.ttl_seconds:
                return deepcopy(cached[1]), self._metadata(age, "FRESH")

            failure = self._failures.get(normalized_key)
            if (
                failure is not None
                and now - failure[0] <= self.failure_retry_seconds
            ):
                self._last_error_type = failure[1]
                if cached is not None and age is not None and age <= self.max_stale_seconds:
                    return deepcopy(cached[1]), self._metadata(age, "STALE")
                raise RuntimeError("persisted summary refresh cooldown is active")

            try:
                value = loader()
                if not isinstance(value, dict):
                    raise TypeError("persisted summary loader must return a dict")
                refreshed_at = monotonic()
                self._entries[normalized_key] = (refreshed_at, deepcopy(value))
                self._failures.pop(normalized_key, None)
                self._refresh_count += 1
                self._last_error_type = None
                self._evict_oldest_locked()
                return deepcopy(value), self._metadata(0.0, "FRESH")
            except Exception as exc:
                self._refresh_errors += 1
                self._last_error_type = type(exc).__name__
                self._failures[normalized_key] = (monotonic(), self._last_error_type)
                while len(self._failures) > self.max_entries:
                    oldest_failure = min(
                        self._failures, key=lambda item: self._failures[item][0]
                    )
                    self._failures.pop(oldest_failure, None)
                if cached is not None and age is not None and age <= self.max_stale_seconds:
                    return deepcopy(cached[1]), self._metadata(age, "STALE")
                raise

    def _metadata(self, age: float | None, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "age_seconds": None if age is None else round(age, 3),
            "ttl_seconds": self.ttl_seconds,
            "max_stale_seconds": self.max_stale_seconds,
            "refresh_count": self._refresh_count,
            "refresh_errors": self._refresh_errors,
            "last_error_type": self._last_error_type,
        }

    def _evict_oldest_locked(self) -> None:
        while len(self._entries) > self.max_entries:
            oldest_key = min(self._entries, key=lambda item: self._entries[item][0])
            self._entries.pop(oldest_key, None)
