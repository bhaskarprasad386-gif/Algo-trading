from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class Candle:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class CandleBuilder:
    """Build fixed-time candles from normalized ticks without database access."""

    def __init__(self, interval_seconds: int = 60) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.interval_seconds = interval_seconds
        self._current: Candle | None = None

    def _bucket(self, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        epoch = int(timestamp.timestamp())
        start = epoch - (epoch % self.interval_seconds)
        return datetime.fromtimestamp(start, tz=timezone.utc)

    def update(self, price: float, volume: float = 0.0, timestamp: datetime | None = None) -> Candle | None:
        if price < 0:
            raise ValueError("price must be non-negative")
        timestamp = timestamp or datetime.now(timezone.utc)
        bucket = self._bucket(timestamp)

        if self._current is None:
            self._current = Candle(bucket, price, price, price, price, volume)
            return None

        if bucket < self._current.start:
            raise ValueError("tick timestamp is older than current candle")

        if bucket == self._current.start:
            self._current.high = max(self._current.high, price)
            self._current.low = min(self._current.low, price)
            self._current.close = price
            self._current.volume += volume
            return None

        completed = self._current
        self._current = Candle(bucket, price, price, price, price, volume)
        return completed

    @property
    def current(self) -> Candle | None:
        return self._current
