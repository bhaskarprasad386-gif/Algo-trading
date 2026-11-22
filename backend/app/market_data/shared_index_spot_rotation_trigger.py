"""Debounced spot-driven option-universe refresh requests for the shared index runner."""
from __future__ import annotations

from math import isfinite
from threading import RLock
from time import monotonic
from typing import Callable


class SpotRotationTrigger:
    """Track movement from the last *successful* rotation anchor.

    Requests are provisional until the controller reports success. Failed rotations
    retain the old anchor and stay pending, so a resolver/network failure cannot
    silently suppress subsequent refresh attempts.
    """

    def __init__(self, *, thresholds: dict[str, float], cooldown_seconds: float = 1.0,
                 clock: Callable[[], float] = monotonic):
        allowed = {"NIFTY", "BANKNIFTY"}
        if not thresholds or set(thresholds) - allowed:
            raise ValueError("thresholds must contain only locked underlyings")
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not isfinite(float(v)) or v <= 0
               for v in thresholds.values()):
            raise ValueError("spot movement thresholds must be finite and positive")
        if isinstance(cooldown_seconds, bool) or not isinstance(cooldown_seconds, (int, float)) or not isfinite(float(cooldown_seconds)) or cooldown_seconds < 0:
            raise ValueError("cooldown must be finite and non-negative")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._thresholds = dict(thresholds)
        self._cooldown = float(cooldown_seconds)
        self._clock = clock
        self._lock = RLock()
        self._last_spot: dict[str, float] = {}
        self._anchor_spot: dict[str, float] = {}
        self._last_request: dict[str, float] = {}
        self._pending: set[str] = set()
        self._inflight: set[str] = set()
        self._rejected = 0
        self._failed = 0

    def on_spot(self, underlying: str, price: float) -> bool:
        symbol = str(underlying).strip().upper()
        if symbol not in self._thresholds:
            return False
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not isfinite(float(price)) or price <= 0:
            return False
        now = self._clock()
        with self._lock:
            self._last_spot[symbol] = float(price)
            if symbol not in self._anchor_spot:
                self._anchor_spot[symbol] = float(price)
                return False
            if symbol in self._inflight:
                self._pending.add(symbol)
                return False
            anchor = self._anchor_spot[symbol]
            if abs(float(price) - anchor) < self._thresholds[symbol]:
                return False
            last = self._last_request.get(symbol)
            if last is not None and now - last < self._cooldown:
                self._pending.add(symbol)
                self._rejected += 1
                return False
            self._last_request[symbol] = now
            self._inflight.add(symbol)
            self._pending.discard(symbol)
            return True

    def rotation_result(self, underlying: str, *, success: bool, spot: float | None = None) -> None:
        """Commit anchor on success; preserve/requeue it on failure."""
        symbol = str(underlying).strip().upper()
        with self._lock:
            if symbol not in self._thresholds or symbol not in self._inflight:
                return
            self._inflight.discard(symbol)
            if success:
                price = spot if spot is not None else self._last_spot.get(symbol)
                if price is not None and isinstance(price, (int, float)) and not isinstance(price, bool) and isfinite(float(price)) and price > 0:
                    self._anchor_spot[symbol] = float(price)
                    # Keep a coalesced follow-up when spot moved again while
                    # the current rotation was resolving/subscribing.
                    latest = self._last_spot.get(symbol)
                    if latest is not None and abs(float(latest) - float(price)) >= self._thresholds[symbol]:
                        self._pending.add(symbol)
                    else:
                        self._pending.discard(symbol)
            else:
                self._pending.add(symbol)
                self._failed += 1

    def flush_due(self) -> tuple[str, ...]:
        """Emit coalesced pending rotations once cooldown expires."""
        now = self._clock()
        with self._lock:
            due = tuple(sorted(symbol for symbol in self._pending
                              if symbol not in self._inflight
                              and now - self._last_request.get(symbol, float("-inf")) >= self._cooldown
                              and symbol in self._last_spot))
            for symbol in due:
                self._last_request[symbol] = now
                self._pending.discard(symbol)
                self._inflight.add(symbol)
            return due

    def snapshot(self) -> dict:
        with self._lock:
            return {"pending_underlyings": sorted(self._pending),
                    "inflight_underlyings": sorted(self._inflight),
                    "rotation_requests": len(self._last_request),
                    "cooldown_rejected_ticks": self._rejected,
                    "rotation_failures": self._failed,
                    "last_spot": dict(self._last_spot),
                    "successful_anchor_spot": dict(self._anchor_spot)}


__all__ = ["SpotRotationTrigger"]
