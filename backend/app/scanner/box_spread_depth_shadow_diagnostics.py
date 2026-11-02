"""Bounded in-memory diagnostics for Box Spread depth shadow comparisons."""
from __future__ import annotations

from collections import Counter, deque
from math import isfinite
from threading import Lock
from typing import Any, Mapping


class BoxDepthShadowDiagnostics:
    """Aggregate comparison outcomes without persisting market data."""

    def __init__(self, *, sample_limit: int = 500) -> None:
        if isinstance(sample_limit, bool) or int(sample_limit) < 1:
            raise ValueError("sample_limit must be a positive integer")
        self._sample_limit = int(sample_limit)
        self._lock = Lock()
        self._status_counts: Counter[str] = Counter()
        self._reason_counts: Counter[str] = Counter()
        self._differences: deque[float] = deque(maxlen=self._sample_limit)

    def record(self, comparison: Mapping[str, Any]) -> None:
        status = str(comparison.get("status") or "UNKNOWN").strip().upper()
        with self._lock:
            self._status_counts[status] += 1
            if status != "COMPLETE":
                reason = str(comparison.get("reason") or "UNSPECIFIED").strip().upper()
                self._reason_counts[reason] += 1
                return
            try:
                difference = float(comparison.get("difference_per_unit"))
            except (TypeError, ValueError, OverflowError):
                return
            if isfinite(difference):
                self._differences.append(difference)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            samples = tuple(self._differences)
            sample_count = len(samples)
            return {
                "status_counts": dict(self._status_counts),
                "incomplete_reasons": dict(self._reason_counts),
                "complete_difference_sample": {
                    "window": "latest_bounded_samples",
                    "limit": self._sample_limit,
                    "count": sample_count,
                    "mean_per_unit": (sum(samples) / sample_count) if sample_count else None,
                    "min_per_unit": min(samples) if samples else None,
                    "max_per_unit": max(samples) if samples else None,
                },
            }


__all__ = ["BoxDepthShadowDiagnostics"]
