"""Bounded, thread-safe Early Impulse Pro signal history and cooldown dedupe."""
from __future__ import annotations
from collections import deque
from math import isfinite
from threading import RLock
from time import monotonic
from typing import Any, Mapping


class EarlyImpulseSignalStore:
    """Stores scanner signals only; no broker or paper execution side effects."""
    def __init__(self, *, max_signals: int = 300, cooldown_seconds: float = 30.0) -> None:
        if isinstance(max_signals, bool) or not isinstance(max_signals, int) or max_signals < 1:
            raise ValueError("max_signals must be a positive integer")
        if isinstance(cooldown_seconds, bool) or not isinstance(cooldown_seconds, (int, float)) or not isfinite(cooldown_seconds) or cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be nonnegative")
        self.max_signals, self.cooldown_seconds = max_signals, float(cooldown_seconds)
        self._lock = RLock()
        self._items: deque[dict[str, Any]] = deque(maxlen=max_signals)
        self._last_by_key: dict[tuple[str, str], float] = {}
        self._suppressed = 0

    @staticmethod
    def _key(signal: Mapping[str, Any]) -> tuple[str, str]:
        underlying = str(signal.get("underlying") or "").strip().upper()
        direction = str(signal.get("direction") or "").strip().upper()
        if not underlying or direction not in {"BUY", "SELL"}:
            raise ValueError("signal requires underlying and BUY/SELL direction")
        return underlying, direction

    def publish(self, signal: Mapping[str, Any], *, now: float | None = None) -> bool:
        key = self._key(signal)
        score = signal.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not isfinite(score) or not 75 <= score <= 100:
            raise ValueError("signal score must be numeric 75..100")
        timestamp = monotonic() if now is None else now
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not isfinite(timestamp):
            raise ValueError("timestamp must be numeric")
        payload = dict(signal)
        payload.update({"underlying": key[0], "direction": key[1], "scanner_only": True, "order_routing_enabled": False})
        with self._lock:
            previous = self._last_by_key.get(key)
            if previous is not None and (timestamp < previous or timestamp - previous < self.cooldown_seconds):
                self._suppressed += 1
                return False
            self._last_by_key[key] = timestamp
            self._items.appendleft(payload)
            if len(self._last_by_key) > self.max_signals * 4:
                retained = {self._key(item) for item in self._items}
                self._last_by_key = {k: v for k, v in self._last_by_key.items() if k in retained}
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"signals": list(self._items), "signal_count": len(self._items),
                    "suppressed_duplicate_count": self._suppressed, "capacity": self.max_signals,
                    "cooldown_seconds": self.cooldown_seconds, "scanner_only": True,
                    "order_routing_enabled": False}

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._last_by_key.clear()
            self._suppressed = 0
