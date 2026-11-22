"""Bounded latest-tick state and freshness metrics for shared index feed."""
from __future__ import annotations

from threading import RLock
from time import time_ns
from typing import Any, Callable

from .shared_cache import put_market_data_record


class SharedIndexMarketDataState:
    MAX_INSTRUMENTS = 50  # locked universe: 48 options + 2 spot



    def __init__(self, *, max_age_seconds: float = 5.0, publish: Callable[[Any], None] | None = None):
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self.max_age_ns = int(max_age_seconds * 1_000_000_000)
        self._lock = RLock()
        self._latest: dict[str, Any] = {}
        self._publish = publish or (lambda _record: None)
        self._accepted = 0
        self._stale_dropped = 0
        self._out_of_order = 0



    def on_record(self, record: Any) -> bool:
        key = getattr(getattr(record, "instrument", None), "value", "")
        ts = getattr(record, "timestamp_ns", None)
        if not key or isinstance(ts, bool) or not isinstance(ts, int) or ts <= 0:
            return False
        now = time_ns()
        if ts > now + 1_000_000_000 or now - ts > self.max_age_ns:
            with self._lock:
                self._stale_dropped += 1
            return False
        with self._lock:
            previous = self._latest.get(key)
            if previous is not None and ts < previous.timestamp_ns:
                self._out_of_order += 1
                return False
            if key not in self._latest and len(self._latest) >= self.MAX_INSTRUMENTS:
                return False
            self._latest[key] = record
            self._accepted += 1
        put_market_data_record(record)
        self._publish(record)
        return True



    def latest(self, key: str) -> Any | None:
        with self._lock:
            record = self._latest.get(str(key))
        if record is None:
            return None
        age_ns = time_ns() - record.timestamp_ns
        if age_ns > self.max_age_ns or age_ns < -1_000_000_000:
            return None
        return record



    def snapshot(self) -> dict[str, Any]:
        now = time_ns()
        with self._lock:
            latest = tuple(self._latest.items())
            accepted = self._accepted
            stale = self._stale_dropped
            out_of_order = self._out_of_order
        ages = {key: max(0, (now - record.timestamp_ns) / 1e9) for key, record in latest}
        fresh = sum(age <= self.max_age_ns / 1e9 for age in ages.values())
        return {
            "instrument_count": len(latest),
            "fresh_count": fresh,
            "stale_count": len(ages) - fresh,
            "feed_fresh": bool(ages) and fresh == len(ages),
            "max_age_seconds": self.max_age_ns / 1e9,
            "max_feed_age_seconds": max(ages.values(), default=None),
            "accepted_ticks": accepted,
            "stale_ticks_dropped": stale,
            "out_of_order_ticks_dropped": out_of_order,
        }
