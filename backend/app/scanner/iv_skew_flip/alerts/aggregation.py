"""Source-time minute bucket aggregation for the NIFTY IV early-entry engine.

Only distinct exchange source timestamps contribute. Repeated sampler/UI reads
cannot manufacture volume, OI, IV or spot observations.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from .data_adapter import option_observation
from .models import OptionObservation, SpotBar

NS_PER_MINUTE = 60_000_000_000


@dataclass
class _SpotBucket:
    start_ns: int
    open: float
    high: float
    low: float
    close: float
    last_source_ns: int


class SourceMinuteAggregator:
    def __init__(self, *, retention_minutes: int = 30) -> None:
        if retention_minutes < 15:
            raise ValueError("retention_minutes must be at least 15")
        self.retention_ns = retention_minutes * NS_PER_MINUTE
        self._options: dict[str, dict[int, OptionObservation]] = defaultdict(dict)
        self._spots: dict[int, _SpotBucket] = {}
        self._last_spot_source_ns = 0

    @staticmethod
    def _minute(timestamp_ns: int) -> int:
        return timestamp_ns // NS_PER_MINUTE * NS_PER_MINUTE

    def ingest(self, samples: tuple[Any, ...] | list[Any], spot: Any, *, now_ns: int) -> None:
        cutoff = now_ns - self.retention_ns
        for sample in samples:
            row = option_observation(sample)
            if row.source_timestamp_ns <= 0 or row.source_timestamp_ns > now_ns:
                continue
            if row.source_timestamp_ns < cutoff:
                continue
            bucket = self._minute(row.source_timestamp_ns)
            prior = self._options[row.token].get(bucket)
            if prior is None or row.source_timestamp_ns > prior.source_timestamp_ns:
                self._options[row.token][bucket] = row

        if spot is not None:
            stamp = int(getattr(spot, "timestamp_ns", 0) or 0)
            price = float(getattr(spot, "price", 0) or 0)
            if 0 < stamp <= now_ns and stamp > self._last_spot_source_ns and price > 0:
                minute = self._minute(stamp)
                current = self._spots.get(minute)
                if current is None:
                    self._spots[minute] = _SpotBucket(minute, price, price, price, price, stamp)
                else:
                    current.high = max(current.high, price)
                    current.low = min(current.low, price)
                    current.close = price
                    current.last_source_ns = stamp
                self._last_spot_source_ns = stamp

        for token in tuple(self._options):
            self._options[token] = {
                stamp: row for stamp, row in self._options[token].items()
                if stamp >= cutoff
            }
            if not self._options[token]:
                del self._options[token]
        self._spots = {stamp: row for stamp, row in self._spots.items() if stamp >= cutoff}

    def option_history(self, token: str, *, now_ns: int) -> tuple[OptionObservation, ...]:
        """Return completed minute closes only; never expose the current partial bucket."""
        current_minute = self._minute(now_ns)
        rows = self._options.get(str(token), {})
        return tuple(rows[key] for key in sorted(rows) if key < current_minute)

    def spot_bars(self, *, now_ns: int) -> tuple[SpotBar, ...]:
        current_minute = self._minute(now_ns)
        return tuple(
            SpotBar(row.start_ns, row.open, row.high, row.low, row.close)
            for stamp, row in sorted(self._spots.items())
            if stamp < current_minute
        )

    def option_tokens(self) -> tuple[str, ...]:
        return tuple(self._options)
