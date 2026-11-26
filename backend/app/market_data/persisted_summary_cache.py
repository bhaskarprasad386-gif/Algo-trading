"""Bounded stale-while-refresh cache for persisted market-data summaries.

Persisted row counts are coverage metadata, not live-feed freshness. Refresh
work runs outside the cache lock so a slow SQLite shard scan cannot block
requests from serving a still-usable last-known summary.
"""
from __future__ import annotations

from copy import deepcopy
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable


class BoundedPersistedSummaryCache:
    """Single-flight, bounded-age cache for read-only persisted summaries."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 60.0,
        max_stale_seconds: float = 300.0,
        max_entries: int = 4,
        failure_retry_seconds: float = 15.0,
        wait_seconds: float = 2.0,
    ) -> None:
        if (
            ttl_seconds <= 0 or max_stale_seconds < ttl_seconds
            or max_entries < 1 or failure_retry_seconds < 0
            or wait_seconds <= 0
        ):
            raise ValueError("invalid persisted summary cache bounds")
        self.ttl_seconds = float(ttl_seconds)
        self.max_stale_seconds = float(max_stale_seconds)
        self.max_entries = int(max_entries)
        self.failure_retry_seconds = float(failure_retry_seconds)
        self.wait_seconds = float(wait_seconds)
        self._lock = Lock()
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}
        self._failures: dict[str, tuple[float, str]] = {}
        self._refreshing: dict[str, Event] = {}
        self._generation = 0
        self._refresh_count = 0
        self._refresh_errors = 0

    def clear(self) -> None:
        """Reset cache state; in-flight workers from the prior generation are ignored."""
        with self._lock:
            self._generation += 1
            self._entries.clear()
            self._failures.clear()
            self._refreshing.clear()
            self._refresh_count = 0
            self._refresh_errors = 0

    def _metadata_locked(
        self, age: float | None, status: str, key: str
    ) -> dict[str, Any]:
        failure = self._failures.get(key)
        return {
            "status": status,
            "age_seconds": None if age is None else round(age, 3),
            "ttl_seconds": self.ttl_seconds,
            "max_stale_seconds": self.max_stale_seconds,
            "refresh_count": self._refresh_count,
            "refresh_errors": self._refresh_errors,
            "last_error_type": None if failure is None else failure[1],
            "refresh_in_progress": key in self._refreshing,
        }

    def _start_refresh_locked(
        self, key: str, loader: Callable[[], dict[str, Any]]
    ) -> Event:
        """Start one bounded worker for this key. Caller holds _lock."""
        event = Event()
        generation = self._generation
        self._refreshing[key] = event
        try:
            worker = Thread(
                target=self._refresh_worker,
                args=(key, loader, event, generation),
                name="persisted-summary-refresh",
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            self._refreshing.pop(key, None)
            self._refresh_errors += 1
            self._failures[key] = (monotonic(), type(exc).__name__)
            self._evict_failures_locked()
            event.set()
        return event

    def _refresh_worker(
        self,
        key: str,
        loader: Callable[[], dict[str, Any]],
        event: Event,
        generation: int,
    ) -> None:
        try:
            value = loader()
            if not isinstance(value, dict):
                raise TypeError("persisted summary loader must return a dict")
            copied_value = deepcopy(value)
            with self._lock:
                if (
                    generation == self._generation
                    and self._refreshing.get(key) is event
                ):
                    self._entries[key] = (monotonic(), copied_value)
                    self._failures.pop(key, None)
                    self._refresh_count += 1
                    self._evict_entries_locked()
        except Exception as exc:
            with self._lock:
                if (
                    generation == self._generation
                    and self._refreshing.get(key) is event
                ):
                    self._refresh_errors += 1
                    self._failures[key] = (monotonic(), type(exc).__name__)
                    self._evict_failures_locked()
        finally:
            with self._lock:
                if self._refreshing.get(key) is event:
                    self._refreshing.pop(key, None)
            event.set()

    def get(
        self,
        key: str,
        loader: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return a defensive copy, serving stale data while one refresh runs."""
        normalized_key = str(key)
        if not normalized_key or len(normalized_key) > 512:
            raise ValueError("persisted summary cache key must be 1..512 characters")

        with self._lock:
            now = monotonic()
            cached = self._entries.get(normalized_key)
            age = None if cached is None else max(0.0, now - cached[0])
            usable_stale = (
                cached is not None and age is not None
                and age <= self.max_stale_seconds
            )
            if cached is not None and age is not None and age <= self.ttl_seconds:
                return deepcopy(cached[1]), self._metadata_locked(
                    age, "FRESH", normalized_key
                )

            failure = self._failures.get(normalized_key)
            if (
                failure is not None
                and now - failure[0] <= self.failure_retry_seconds
            ):
                if usable_stale:
                    return deepcopy(cached[1]), self._metadata_locked(
                        age, "STALE", normalized_key
                    )
                raise RuntimeError(
                    f"persisted summary refresh cooldown active ({failure[1]})"
                )

            event = self._refreshing.get(normalized_key)
            if event is None:
                if len(self._refreshing) >= self.max_entries:
                    if usable_stale:
                        return deepcopy(cached[1]), self._metadata_locked(
                            age, "STALE", normalized_key
                        )
                    raise RuntimeError("persisted summary refresh capacity exhausted")
                event = self._start_refresh_locked(normalized_key, loader)

            # A valid last-known summary must never wait behind its refresh.
            if usable_stale:
                return deepcopy(cached[1]), self._metadata_locked(
                    age, "STALE", normalized_key
                )

        # Cold or hard-expired callers wait only for a fixed budget. The worker
        # performs SQLite work without holding the cache lock.
        finished = event.wait(self.wait_seconds)
        with self._lock:
            now = monotonic()
            cached = self._entries.get(normalized_key)
            age = None if cached is None else max(0.0, now - cached[0])
            if (
                cached is not None and age is not None
                and age <= self.max_stale_seconds
            ):
                status = "FRESH" if age <= self.ttl_seconds else "STALE"
                return deepcopy(cached[1]), self._metadata_locked(
                    age, status, normalized_key
                )
            failure = self._failures.get(normalized_key)
            if failure is not None:
                raise RuntimeError(
                    f"persisted summary refresh failed ({failure[1]})"
                )
            if not finished:
                raise TimeoutError(
                    "persisted summary refresh did not finish within its bound"
                )
            raise RuntimeError("persisted summary refresh completed without a value")

    def _evict_entries_locked(self) -> None:
        while len(self._entries) > self.max_entries:
            oldest_key = min(self._entries, key=lambda item: self._entries[item][0])
            self._entries.pop(oldest_key, None)

    def _evict_failures_locked(self) -> None:
        while len(self._failures) > self.max_entries:
            oldest_key = min(self._failures, key=lambda item: self._failures[item][0])
            self._failures.pop(oldest_key, None)
