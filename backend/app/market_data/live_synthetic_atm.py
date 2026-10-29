"""Source-backed live ATM tracker for synthetic scanning.

The tracker only derives ATM from observed underlying prices and a concrete
strike set from the Angel One instrument master. It never invents a strike.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime
from threading import Lock
from time import time_ns


def _expiry_matches(left: object, right: object) -> bool:
    """Compare Angel One expiry values by calendar date, not display format."""
    def parsed(value: object):
        text = str(value or "").strip().upper()
        for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    left_date, right_date = parsed(left), parsed(right)
    if left_date is not None and right_date is not None:
        return left_date == right_date
    return str(left or "").strip().upper() == str(right or "").strip().upper()


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
    result: dict[str, set[float]] = {symbol: set() for symbol in requested}
    for item in instruments:
        symbol = str(item.get("name") or "").strip().upper()
        if symbol not in result:
            continue
        if str(item.get("exch_seg") or "").strip().upper() != str(exchange_segment).strip().upper():
            continue
        if not _expiry_matches(item.get("expiry"), expiry):
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
        # Keep source time with the price so a dead underlying feed cannot
        # leave an apparently valid ATM strike indefinitely.
        self._prices: dict[str, tuple[float, int]] = {}
        self._lock = Lock()

    def update(
        self, symbol: str, price: float, timestamp_ns: int | None = None
    ) -> float | None:
        key = str(symbol).strip().upper()
        if key not in self._strikes:
            return None
        value = float(price)
        if value <= 0:
            raise ValueError("underlying price must be positive")
        source_ns = int(timestamp_ns) if timestamp_ns is not None else time_ns()
        if source_ns <= 0:
            raise ValueError("underlying timestamp must be positive")
        # Reject future-skewed ticks before they poison the ordering watermark.
        if source_ns > time_ns() + 1_000_000_000:
            return self.atm(key)
        with self._lock:
            previous = self._prices.get(key)
            older_tick = previous is not None and source_ns < previous[1]
            if not older_tick:
                self._prices[key] = (value, source_ns)
        # Do not call atm() while holding the non-reentrant tracker lock.
        return self.atm(key)

    def atm(
        self,
        symbol: str,
        _timestamp_ns: int | None = None,
        *,
        max_age_seconds: float = 5.0,
    ) -> float | None:
        key = str(symbol).strip().upper()
        strikes = self._strikes.get(key)
        if strikes is None:
            return None
        with self._lock:
            quote = self._prices.get(key)
        if quote is None:
            return None
        price, source_ns = quote
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")
        timestamp_ns = int(_timestamp_ns) if _timestamp_ns is not None else 0
        # Legacy runner callbacks use 0 to mean "no evaluation timestamp".
        # Treat that sentinel as wall-clock now, not as an epoch timestamp.
        now_ns = timestamp_ns if timestamp_ns > 0 else time_ns()
        age_ns = now_ns - source_ns
        # NSE spot and NFO derivative exchange timestamps can diverge while
        # both feeds are still live. Allow a bounded 5s lead for the spot tick
        # when evaluating a derivative snapshot; stale spot data is still
        # rejected by this same 5s freshness window in the other direction.
        # This avoids dropping every scan for a symbol solely because its
        # latest spot timestamp is newer than the option/future snapshot.
        max_future_skew_ns = 5_000_000_000
        if age_ns < -max_future_skew_ns or age_ns > int(max_age_seconds * 1_000_000_000):
            return None
        index = bisect_left(strikes, price)
        if index == 0:
            return strikes[0]
        if index == len(strikes):
            return strikes[-1]
        lower, upper = strikes[index - 1], strikes[index]
        return lower if price - lower <= upper - price else upper

    def spot_snapshot(
        self, symbol: str, now_ns: int | None = None, *, max_age_seconds: float = 5.0
    ) -> dict | None:
        """Return the source-backed underlying spot only while its tick is fresh."""
        key = str(symbol).strip().upper()
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")
        with self._lock:
            quote = self._prices.get(key)
        if quote is None:
            return None
        price, source_ns = quote
        now = int(now_ns) if now_ns is not None else time_ns()
        age_seconds = (now - source_ns) / 1_000_000_000
        if age_seconds < -5.0 or age_seconds > max_age_seconds:
            return None
        return {
            "spot_price": float(price),
            "spot_timestamp_ns": int(source_ns),
            "spot_age_seconds": round(max(0.0, age_seconds), 3),
        }

    def diagnostics_snapshot(self, now_ns: int | None = None) -> dict:
        """Expose chain coverage and live ATM readiness without inventing quotes."""
        now = int(now_ns) if now_ns is not None else time_ns()
        with self._lock:
            prices = dict(self._prices)
            strike_sets = dict(self._strikes)
        symbols = []
        for symbol, strikes in sorted(strike_sets.items()):
            quote = prices.get(symbol)
            source_ns = int(quote[1]) if quote else None
            age_seconds = (
                (now - source_ns) / 1_000_000_000
                if source_ns is not None else None
            )
            atm_strike = self.atm(symbol, now)
            symbols.append({
                "underlying": symbol,
                "concrete_strike_count": len(strikes),
                "has_underlying_price": quote is not None,
                "last_underlying_price": float(quote[0]) if quote else None,
                "source_age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
                "atm_strike": atm_strike,
                "atm_ready": atm_strike is not None,
            })
        return {
            "tracked_symbol_count": len(strike_sets),
            "atm_ready_symbol_count": sum(1 for item in symbols if item["atm_ready"]),
            "symbols_without_concrete_chain": [],
            "symbols": symbols,
        }


__all__ = ["LiveSyntheticAtmTracker", "concrete_strikes_from_master"]
