from __future__ import annotations

from typing import Any, Callable, Optional

from app.market_data.tick_engine import TickEngine
from app.market_data.websocket import MarketDataWebSocket


class MarketDataEngine:
    """Connect broker ticks to the in-memory tick processor."""

    def __init__(self, websocket: Optional[MarketDataWebSocket] = None, max_ticks_per_symbol: int = 100):
        self.websocket = websocket or MarketDataWebSocket()
        self.tick_engine = TickEngine(max_ticks_per_symbol=max_ticks_per_symbol)
        self._external_callback: Optional[Callable[[dict[str, Any]], None]] = None

    def _handle_tick(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        tick = self.tick_engine.process(message)
        if tick is not None and self._external_callback:
            self._external_callback(tick)

    def start(
        self,
        exchange_type: int,
        tokens: list[str],
        mode: int = 1,
        correlation_id: str = "market-data",
        on_tick: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self._external_callback = on_tick
        self.websocket.connect(
            exchange_type=exchange_type,
            tokens=tokens,
            mode=mode,
            correlation_id=correlation_id,
            on_data=self._handle_tick,
        )

    def subscribe(self, tokens: list[str], mode: Optional[int] = None) -> None:
        self.websocket.subscribe(tokens, mode=mode)

    def unsubscribe(self, tokens: list[str]) -> None:
        self.websocket.unsubscribe(tokens)

    def latest(self, symbol: str) -> dict[str, Any] | None:
        return self.tick_engine.latest(symbol)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return self.tick_engine.snapshot()

    def history(self, symbol: str, limit: int | None = None) -> list[dict[str, Any]]:
        return self.tick_engine.history(symbol, limit=limit)

    def close(self) -> None:
        self.websocket.close()
