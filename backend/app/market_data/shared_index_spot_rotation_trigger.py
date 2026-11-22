"""Debounced spot-driven option-universe refresh requests for the shared index runner."""
from __future__ import annotations

from threading import RLock
from time import monotonic
from typing import Callable



class SpotRotationTrigger:
    """Request ATM option rotation only after meaningful spot movement and cooldown.

    This class does not select contracts or subscribe/unsubscribe sockets; callers
    consume the returned underlying names and perform resolver work separately.
    """

    def __init__(self, *, thresholds: dict[str, float], cooldown_seconds: float = 1.0,
                 clock: Callable[[], float] = monotonic):
        allowed = {"NIFTY", "BANKNIFTY"}
        if not thresholds or set(thresholds) - allowed:
            raise ValueError("thresholds must contain only locked underlyings")
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 for v in thresholds.values()):
            raise ValueError("spot movement thresholds must be positive")
        if cooldown_seconds < 0:
            raise ValueError("cooldown must be non-negative")
        self._thresholds = dict(thresholds)
        self._cooldown = float(cooldown_seconds)
        self._clock = clock
        self._lock = RLock()
        self._last_spot: dict[str, float] = {}
        self._anchor_spot: dict[str, float] = {}
        self._last_request: dict[str, float] = {}
        self._pending: set[str] = set()
        self._rejected = 0



    def on_spot(self, underlying: str, price: float) -> bool:
        symbol = str(underlying).strip().upper()
        if symbol not in self._thresholds:
            return False
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            return False
        now = self._clock()
        with self._lock:
            previous = self._last_spot.get(symbol)
            self._last_spot[symbol] = float(price)
            if previous is None:
                self._anchor_spot[symbol] = float(price)
                return False
            anchor = self._anchor_spot.get(symbol, previous)
            if abs(float(price) - anchor) < self._thresholds[symbol]:
                return False
            last = self._last_request.get(symbol)
            if last is not None and now - last < self._cooldown:
                self._pending.add(symbol)
                self._rejected += 1
                return False
            self._last_request[symbol] = now
            self._anchor_spot[symbol] = float(price)
            self._pending.discard(symbol)
            return True



    def flush_due(self) -> tuple[str, ...]:
        """Emit coalesced pending rotations once cooldown expires."""
        now = self._clock()
        with self._lock:
            due = tuple(sorted(symbol for symbol in self._pending
                              if now - self._last_request.get(symbol, float("-inf")) >= self._cooldown))
            for symbol in due:
                self._last_request[symbol] = now
                self._anchor_spot[symbol] = self._last_spot.get(symbol, self._anchor_spot.get(symbol, 0.0))
                self._pending.discard(symbol)
            return due



    def snapshot(self) -> dict:
        with self._lock:
            return {"pending_underlyings": sorted(self._pending),
                    "rotation_requests": len(self._last_request),
                    "cooldown_rejected_ticks": self._rejected,
                    "last_spot": dict(self._last_spot)}



__all__ = ["SpotRotationTrigger"]
