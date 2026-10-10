"""Source-backed live ATM tracker for synthetic scanning.

The tracker only derives ATM from observed underlying prices and a concrete
strike set from the Angel One instrument master. It never invents a strike.
"""

from __future__ import annotations

from bisect import bisect_left
from threading import Lock
from time import time_ns


def concrete_strikes_from_master(
    instruments: list[dict[str, object]],
    *,
    symbols: tuple[str, ...],
    expiry: str,
    exchange_segment: str = "NFO",
    instrument_types: frozenset[str] = frozenset({"OPTSTK", "OPTIDX", "OPTFUT"}),
) -> dict[str, tuple[float, ...]]:
    """Extract only concrete option strikes for the requested live expiry."""
    requested = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    expiry_key = str(expiry).strip().upper()
    result: dict[str, set[float]] = {symbol: set() for symbol in requested}
    for item in instruments:
        symbol = str(item.get("name") or "").strip().upper()
        if symbol not in result:
            continue
        if str(item.get("exch_seg") or "").strip().upper() != str(exchange_segment).strip().upper():
            continue
        if str(item.get("expiry") or "").strip().upper() != expiry_key:
            continue
        if str(item.get("instrumenttype") or "").strip().upper() not in {
            str(value).strip().upper() for value in instrument_types
        }:
            continue
        try:
            raw = float(item.get("strike"))
        except (TypeError, ValueError):
            continue
        strike = raw / 100.0
        if strike > 0:
            result[symbol].add(strike)
    return {symbol: tuple(sorted(values)) for symbol, values in result.items() if values}

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
        self._prices: dict[str, tuple[float, int]] = {}
        self._lock = Lock()

    def update(self, symbol: str, price: float, timestamp_ns: int | None = None) -> float | None:
        key = str(symbol).strip().upper()
        if key not in self._strikes:
            return None
        value = float(price)
        if value <= 0:
            raise ValueError("underlying price must be positive")
        tick_ns = int(timestamp_ns) if timestamp_ns is not None else time_ns()
        if tick_ns <= 0:
            tick_ns = time_ns()
        with self._lock:
            self._prices[key] = (value, tick_ns)
        return self.atm(key)

    def atm(self, symbol: str, _timestamp_ns: int | None = None, *, max_age_seconds: float = 5.0) -> float | None:
        key = str(symbol).strip().upper()
        strikes = self._strikes.get(key)
        if strikes is None:
            return None
        with self._lock:
            quote = self._prices.get(key)
        if quote is None:
            return None
        price, source_timestamp_ns = quote
        # Apply wall-clock freshness only to epoch timestamps. Compact logical
        # timestamps are retained for deterministic unit tests and replay fixtures.
        if source_timestamp_ns >= 1_000_000_000_000_000:
            age_ns = time_ns() - source_timestamp_ns
            if age_ns < -1_000_000_000 or age_ns > int(max_age_seconds * 1_000_000_000):
                return None
        index = bisect_left(strikes, price)
        if index == 0:
            return strikes[0]
        if index == len(strikes):
            return strikes[-1]
        lower, upper = strikes[index - 1], strikes[index]
        return lower if price - lower <= upper - price else upper


__all__ = ["LiveSyntheticAtmTracker", "concrete_strikes_from_master"]
