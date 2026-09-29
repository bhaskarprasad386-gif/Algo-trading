"""Bounded shared market-data cache with a Redis-ready interface.

The in-process implementation stores only the latest snapshot per key. It is
deliberately bounded so live strategies cannot grow process memory by retaining
historical ticks. A future Redis backend can implement the same protocol
without changing strategy consumers.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import threading
import time
from typing import Any, Protocol


class MarketDataCache(Protocol):
    def put(self, key: str, value: dict[str, Any], *, timestamp_ns: int | None = None) -> None: ...
    def get(self, key: str) -> dict[str, Any] | None: ...
    def delete(self, key: str) -> bool: ...
    def clear(self) -> None: ...
    def stats(self) -> dict[str, int]: ...


@dataclass(frozen=True)
class CacheEntry:
    value: dict[str, Any]
    timestamp_ns: int
    expires_at_monotonic: float


class BoundedMarketDataCache:
    """Thread-safe latest-snapshot cache with hard entry and TTL bounds."""

    def __init__(self, *, max_entries: int = 2000, ttl_seconds: float = 15.0) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.max_entries = int(max_entries)
        self.ttl_seconds = float(ttl_seconds)
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._evictions = 0
        self._expired = 0

    def _purge_expired_locked(self, now: float) -> None:
        expired = [key for key, entry in self._entries.items() if entry.expires_at_monotonic <= now]
        for key in expired:
            self._entries.pop(key, None)
        self._expired += len(expired)

    def put(self, key: str, value: dict[str, Any], *, timestamp_ns: int | None = None) -> None:
        key = str(key).strip()
        if not key:
            raise ValueError("cache key is required")
        if not isinstance(value, dict):
            raise TypeError("cache value must be a dict")
        now = time.monotonic()
        timestamp = time.time_ns() if timestamp_ns is None else int(timestamp_ns)
        if timestamp < 0:
            raise ValueError("timestamp_ns cannot be negative")
        entry = CacheEntry(deepcopy(value), timestamp, now + self.ttl_seconds)
        with self._lock:
            self._purge_expired_locked(now)
            self._entries.pop(key, None)
            self._entries[key] = entry
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
                self._evictions += 1

    def get(self, key: str) -> dict[str, Any] | None:
        key = str(key).strip()
        if not key:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at_monotonic <= now:
                self._entries.pop(key, None)
                self._expired += 1
                return None
            self._entries.move_to_end(key)
            return deepcopy(entry.value)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._entries.pop(str(key).strip(), None) is not None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._purge_expired_locked(time.monotonic())
            return {
                "entries": len(self._entries),
                "max_entries": self.max_entries,
                "evictions": self._evictions,
                "expired": self._expired,
            }


_SHARED_CACHE = BoundedMarketDataCache()


def get_shared_market_data_cache() -> BoundedMarketDataCache:
    return _SHARED_CACHE
