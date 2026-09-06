"""Conservative pacing for Angel One historical candle requests."""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class HistoricalRateLimit:
    requests_per_second: int = 3
    requests_per_minute: int = 150
    requests_per_hour: int = 5000


class HistoricalRateLimiter:
    """Client-side limiter with conservative one-request-at-a-time pacing."""

    def __init__(self, limit: HistoricalRateLimit | None = None, *, safety_delay_seconds: float = 0.40) -> None:
        self.limit = limit or HistoricalRateLimit()
        if self.limit.requests_per_second <= 0 or self.limit.requests_per_minute <= 0:
            raise ValueError("rate limits must be positive")
        if safety_delay_seconds < 0:
            raise ValueError("safety delay cannot be negative")
        self.safety_delay_seconds = safety_delay_seconds
        self._lock = Lock()
        self._timestamps: list[float] = []

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                self._timestamps = [t for t in self._timestamps if now - t < 3600]
                recent_minute = [t for t in self._timestamps if now - t < 60]
                recent_second = [t for t in self._timestamps if now - t < 1]
                waits = [self.safety_delay_seconds - (now - self._timestamps[-1])] if self._timestamps else [0.0]
                if len(recent_minute) >= self.limit.requests_per_minute:
                    waits.append(60 - (now - recent_minute[0]))
                if len(recent_second) >= self.limit.requests_per_second:
                    waits.append(1 - (now - recent_second[0]))
                wait = max(0.0, *waits)
                if wait <= 0:
                    self._timestamps.append(now)
                    return
                time.sleep(wait)
