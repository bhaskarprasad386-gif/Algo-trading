"""Bounded asyncio event-loop lag monitor."""
from __future__ import annotations

import asyncio
from collections import Counter
import time
import threading
from typing import Any


class EventLoopLagMonitor:
    BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5)

    def __init__(self, interval_seconds: float = 1.0) -> None:
        if interval_seconds <= 0:
            raise ValueError("event-loop monitor interval must be positive")
        self.interval_seconds = float(interval_seconds)
        self._counts: Counter[str] = Counter()
        self._samples = 0
        self._total_lag = 0.0
        self._max_lag = 0.0
        self._last_lag = 0.0
        self._started_at = time.time()
        self._lock = threading.Lock()

    def observe(self, lag_seconds: float) -> None:
        lag = max(0.0, float(lag_seconds))
        bucket = next((f"le_{bound:g}s" for bound in self.BUCKETS if lag <= bound), "gt_0.5s")
        with self._lock:
            self._counts[bucket] += 1
            self._samples += 1
            self._total_lag += lag
            self._max_lag = max(self._max_lag, lag)
            self._last_lag = lag

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.interval_seconds
        while True:
            await asyncio.sleep(max(0.0, deadline - loop.time()))
            now = loop.time()
            self.observe(max(0.0, now - deadline))
            deadline += self.interval_seconds
            # Do not spin to catch up after a blocked event loop.
            if deadline <= now:
                deadline = now + self.interval_seconds

    def snapshot(self) -> dict[str, Any]:
        # Histogram is bounded to seven fixed buckets; p95 is an upper-bound
        # estimate from the first bucket crossing the 95th percentile.
        with self._lock:
            samples = self._samples
            counts = dict(self._counts)
            total_lag = self._total_lag
            max_lag = self._max_lag
            last_lag = self._last_lag
        target = max(1, int(samples * 0.95 + 0.999))
        cumulative = 0
        p95_bucket = None
        bounds = (*self.BUCKETS, float("inf"))
        labels = [f"le_{bound:g}s" for bound in self.BUCKETS] + ["gt_0.5s"]
        for label, bound in zip(labels, bounds):
            cumulative += counts.get(label, 0)
            if cumulative >= target:
                p95_bucket = label
                break
        return {
            "sample_interval_seconds": self.interval_seconds,
            "sample_count": samples,
            "last_lag_seconds": round(last_lag, 4) if samples else None,
            "average_lag_seconds": round(total_lag / samples, 4) if samples else None,
            "max_lag_seconds": round(max_lag, 4) if samples else None,
            "p95_lag_bucket": p95_bucket,
            "lag_buckets": counts,
            "started_at_epoch_seconds": self._started_at,
        }
