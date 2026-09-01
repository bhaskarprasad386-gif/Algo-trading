from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.market_data.candle_builder import Candle, CandleBuilder


class CandlePipeline:
    """Convert normalized ticks into completed candles, kept independent of storage."""

    def __init__(self, interval_seconds: int = 60):
        self.interval_seconds = interval_seconds
        self._builders: dict[str, CandleBuilder] = {}

    @staticmethod
    def _price(tick: dict[str, Any]) -> float:
        value = tick.get("ltp")
        try:
            price = float(value)
        except (TypeError, ValueError):
            raise ValueError("tick requires numeric ltp") from None
        if price < 0:
            raise ValueError("tick ltp must be non-negative")
        return price

    @staticmethod
    def _timestamp(tick: dict[str, Any]) -> datetime:
        value = tick.get("timestamp") or tick.get("exchange_timestamp")
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)

    def update(self, tick: dict[str, Any]) -> Candle | None:
        symbol = str(tick.get("symbol") or tick.get("tradingSymbol") or tick.get("token") or "").strip()
        if not symbol:
            return None
        builder = self._builders.setdefault(symbol, CandleBuilder(self.interval_seconds))
        volume = float(tick.get("volume") or 0.0)
        return builder.update(self._price(tick), volume, self._timestamp(tick))

    def current(self, symbol: str) -> Candle | None:
        builder = self._builders.get(symbol)
        return builder.current if builder else None
