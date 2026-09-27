"""Source-backed live ATM tracker for synthetic scanning.

The tracker only derives ATM from observed underlying prices and a concrete
strike set from the Angel One instrument master. It never invents a strike.
"""

from __future__ import annotations

from bisect import bisect_left
from threading import Lock


class LiveSyntheticAtmTracker:
    """Keep the latest underlying price and nearest real chain strike."""

    def __init__(self, *, strikes_by_symbol: dict[str, tuple[float, ...]]) -> None:
        normalized: dict[str, tuple[float, ...]] = {}
        for symbol, strikes in strikes_by_symbol.items():
            key = str(symbol).strip().upper()
            values = tuple(sorted({float(strike) for strike in strikes}))
            if not key or not values:
                raise ValueError("each ATM symbol requires at least one concrete strike")
            if any(value <= 0 for value in values):
                raise ValueError("ATM strikes must be positive")
            normalized[key] = values
        if not normalized:
            raise ValueError("at least one ATM symbol is required")
        self._strikes = normalized
        self._prices: dict[str, float] = {}
        self._lock = Lock()

    def update(self, symbol: str, price: float) -> float | None:
        key = str(symbol).strip().upper()
        if key not in self._strikes:
            return None
        value = float(price)
        if value <= 0:
            raise ValueError("underlying price must be positive")
        with self._lock:
            self._prices[key] = value
        return self.atm(key)

    def atm(self, symbol: str, _timestamp_ns: int | None = None) -> float | None:
        key = str(symbol).strip().upper()
        strikes = self._strikes.get(key)
        if strikes is None:
            return None
        with self._lock:
            price = self._prices.get(key)
        if price is None:
            return None
        index = bisect_left(strikes, price)
        if index == 0:
            return strikes[0]
        if index == len(strikes):
            return strikes[-1]
        lower, upper = strikes[index - 1], strikes[index]
        return lower if price - lower <= upper - price else upper


__all__ = ["LiveSyntheticAtmTracker"]
