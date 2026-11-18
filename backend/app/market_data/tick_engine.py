from __future__ import annotations

from collections import deque
from threading import Lock
from time import time_ns
from typing import Any


class TickEngine:
    """In-memory tick processor kept independent from broker and API layers."""

    def __init__(self, max_ticks_per_symbol: int = 100):
        if max_ticks_per_symbol < 1:
            raise ValueError("max_ticks_per_symbol must be >= 1")
        self._max_ticks = max_ticks_per_symbol
        self._ticks: dict[str, deque[dict[str, Any]]] = {}
        self._latest: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    @staticmethod
    def _symbol(message: dict[str, Any]) -> str:
        return str(
            message.get("symbol")
            or message.get("tradingSymbol")
            or message.get("token")
            or ""
        ).strip()

    def process(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize and store a broker tick; return None for unusable messages."""
        symbol = self._symbol(message)
        if not symbol:
            return None

        tick = dict(message)
        tick["symbol"] = symbol
        tick["received_at_ns"] = time_ns()

        with self._lock:
            history = self._ticks.setdefault(
                symbol, deque(maxlen=self._max_ticks)
            )
            history.append(tick)
            self._latest[symbol] = tick
        return tick

    def latest(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            tick = self._latest.get(symbol)
            return dict(tick) if tick else None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {symbol: dict(tick) for symbol, tick in self._latest.items()}

    def history(self, symbol: str, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._ticks.get(symbol, ()))
        if limit is not None:
            if limit < 1:
                return []
            values = values[-limit:]
        return [dict(value) for value in values]

    def clear(self) -> None:
        with self._lock:
            self._ticks.clear()
            self._latest.clear()
