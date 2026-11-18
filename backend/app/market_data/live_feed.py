"""Strategy-neutral live feed bridge.

Connects the existing Angel One WebSocket/TickEngine to the durable live
recorder and an optional strategy callback. No broker order routing occurs.
"""

from __future__ import annotations

from typing import Any, Callable

from app.backtesting.historical_catalog import HistoricalCatalog
from app.market_data.engine import MarketDataEngine
from app.market_data.live_recorder import LiveMarketDataRecorder


class LiveMarketDataFeed:
    """One live tick path shared by scanners, paper strategies and storage."""

    def __init__(
        self,
        catalog: HistoricalCatalog,
        *,
        engine: MarketDataEngine | None = None,
        recorder: LiveMarketDataRecorder | None = None,
        on_tick: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.engine = engine or MarketDataEngine()
        self.recorder = recorder or LiveMarketDataRecorder(catalog)
        self.on_tick = on_tick

    def _handle_tick(self, tick: dict[str, Any]) -> None:
        self.recorder.on_tick(tick)
        if self.on_tick is not None:
            self.on_tick(tick)

    def start(
        self,
        *,
        exchange_type: int,
        tokens: list[str],
        mode: int = 1,
        correlation_id: str = "live-market-data",
    ) -> None:
        self.engine.start(
            exchange_type=exchange_type,
            tokens=tokens,
            mode=mode,
            correlation_id=correlation_id,
            on_tick=self._handle_tick,
        )

    def subscribe(self, tokens: list[str], mode: int | None = None) -> None:
        self.engine.subscribe(tokens, mode=mode)

    def unsubscribe(self, tokens: list[str]) -> None:
        self.engine.unsubscribe(tokens)

    def flush(self) -> int:
        return self.recorder.flush()

    def latest(self, symbol: str) -> dict[str, Any] | None:
        return self.engine.latest(symbol)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return self.engine.snapshot()

    def close(self) -> None:
        try:
            self.recorder.flush()
        finally:
            self.engine.close()
